import json
import random
import torch
import torch.utils.data as data
from collections import OrderedDict
from embeddings import GloveEmbedding, KazumaCharEmbedding
from tqdm import tqdm
import os
import pickle
from random import shuffle

from utils.config import args, PAD_token, SOS_token, EOS_token, UNK_token
from .fix_label import fix_general_label_error
from utils.data_utils import convert_examples_to_features
from transformers.tokenization_bert import BertTokenizer

EXPERIMENT_DOMAINS = ["hotel", "train", "restaurant", "attraction", "taxi"]

class Lang:
    def __init__(self):
        self.word2index = {}
        self.index2word = {PAD_token: "PAD", SOS_token: "SOS", EOS_token: "EOS", UNK_token: 'UNK'}
        self.n_words = len(self.index2word) # Count default tokens
        self.word2index = dict([(v, k) for k, v in self.index2word.items()])
      
    def index_words(self, sent, type):
        if type == 'utter':
            for word in sent.split(" "):
                self.index_word(word)
        elif type == 'slot':
            for slot in sent:
                d, s = slot.split("-")
                self.index_word(d)
                for ss in s.split(" "):
                    self.index_word(ss)
        elif type == 'belief':
            for slot, value in sent.items():
                d, s = slot.split("-")
                self.index_word(d)
                for ss in s.split(" "):
                    self.index_word(ss)
                for v in value.split(" "):
                    self.index_word(v)

    def index_word(self, word):
        if word not in self.word2index:
            self.word2index[word] = self.n_words
            self.index2word[self.n_words] = word
            self.n_words += 1


class Dataset(data.Dataset):
    """Custom data.Dataset compatible with data.DataLoader."""
    def __init__(self, data_info, src_word2id, trg_word2id, sequicity, mem_word2id):
        """Reads source and target sequences from txt files."""
        self.ID = data_info['ID']
        self.turn_domain = data_info['turn_domain']
        self.turn_id = data_info['turn_id']
        self.dialog_history = data_info['dialog_history']
        self.turn_belief = data_info['turn_belief']
        self.gating_label = data_info['gating_label']
        self.turn_uttr = data_info['turn_uttr']
        self.generate_y = data_info["generate_y"]
        self.sequicity = sequicity
        self.num_total_seqs = len(self.dialog_history)
        self.src_word2id = src_word2id
        self.trg_word2id = trg_word2id
        self.mem_word2id = mem_word2id
    
    def __getitem__(self, index):
        """Returns one data pair (source and target)."""
        ID = self.ID[index]
        turn_id = self.turn_id[index]
        turn_belief = self.turn_belief[index]
        gating_label = self.gating_label[index]
        turn_uttr = self.turn_uttr[index]
        turn_domain = self.preprocess_domain(self.turn_domain[index])
        generate_y = self.generate_y[index]
        generate_y = self.preprocess_slot(generate_y, self.trg_word2id)
        context_plain = self.dialog_history[index]
        context = self.preprocess(context_plain, self.src_word2id)

        
        item_info = {
            "ID":ID, 
            "turn_id":turn_id, 
            "turn_belief":turn_belief, 
            "gating_label":gating_label, 
            "context":context, 
            "context_plain":context_plain, 
            "turn_uttr_plain":turn_uttr, 
            "turn_domain":turn_domain, 
            "generate_y":generate_y,
            }
        return item_info

    def __len__(self):
        return self.num_total_seqs
    
    def preprocess(self, sequence, word2idx):
        """Converts words to ids."""
        story = [word2idx[word] if word in word2idx else UNK_token for word in sequence.split()]
        story = torch.Tensor(story)
        return story

    def preprocess_slot(self, sequence, word2idx):
        """Converts words to ids."""
        story = []
        for value in sequence:
            v = [word2idx[word] if word in word2idx else UNK_token for word in value.split()] + [EOS_token]
            story.append(v)
        return story

    def preprocess_memory(self, sequence, word2idx):
        """Converts words to ids."""
        story = []
        for value in sequence:
            d, s, v = value
            s = s.replace("book","").strip()
            # separate each word in value to different memory slot
            for wi, vw in enumerate(v.split()):
                idx = [word2idx[word] if word in word2idx else UNK_token for word in [d, s, "t{}".format(wi), vw]]
                story.append(idx)
        story = torch.Tensor(story)
        return story

    def preprocess_domain(self, turn_domain):
        domains = {"attraction":0, "restaurant":1, "taxi":2, "train":3, "hotel":4, "hospital":5, "bus":6, "police":7}
        return domains[turn_domain]


def collate_fn(data, tokenizer=None):
    def merge(sequences, is_context=False, plain=False):
        '''
        merge from batch * sent_len to batch * max_len 
        '''

        new_sequences = sequences
        if is_context:
            lengths = [len(seq) for seq in sequences]
            if args['max_context_length'] == -1:
                new_sequences = sequences
            else:
                max_len = args['max_context_length']
                new_sequences = []
                for i, seq in enumerate(sequences):
                    if lengths[i] > max_len:
                        new_sequences.append(seq[lengths[i] - max_len:])
                    else:
                        new_sequences.append(seq)

        new_lengths = [len(seq) for seq in new_sequences]
        max_len = 1 if max(new_lengths)==0 else max(new_lengths)

        if plain:
            final_seqs = []
            for i, seq in enumerate(new_sequences):
                end = new_lengths[i]
                final_seqs.append(seq[:end])
            return final_seqs, new_lengths
        else:
            padded_seqs = torch.ones(len(sequences), max_len).long()
            for i, seq in enumerate(new_sequences):
                end = new_lengths[i]
                padded_seqs[i, :end] = seq[:end]

            padded_seqs = padded_seqs.detach()
            return padded_seqs, new_lengths

    def merge_multi_response(sequences):
        '''
        merge from batch * nb_slot * slot_len to batch * nb_slot * max_slot_len
        '''
        lengths = []
        for bsz_seq in sequences:
            length = [len(v) for v in bsz_seq]
            lengths.append(length)
        max_len = max([max(l) for l in lengths])
        padded_seqs = []
        for bsz_seq in sequences:
            pad_seq = []
            for v in bsz_seq:
                v = v + [PAD_token] * (max_len-len(v))
                pad_seq.append(v)
            padded_seqs.append(pad_seq)
        padded_seqs = torch.tensor(padded_seqs)
        lengths = torch.tensor(lengths)
        return padded_seqs, lengths

    def merge_memory(sequences):
        lengths = [len(seq) for seq in sequences]
        max_len = 1 if max(lengths)==0 else max(lengths) # avoid the empty belief state issue
        padded_seqs = torch.ones(len(sequences), max_len, 4).long()
        for i, seq in enumerate(sequences):
            end = lengths[i]
            if len(seq) != 0:
                padded_seqs[i,:end,:] = seq[:end]
        return padded_seqs, lengths
  
    # sort a list by sequence length (descending order) to use pack_padded_sequence
    data.sort(key=lambda x: len(x['context']), reverse=True) 
    item_info = {}
    for key in data[0].keys():
        item_info[key] = [d[key] for d in data]

    # merge sequences
    src_seqs, src_lengths = merge(item_info['context'], is_context=True, plain=False)
    context_plain_tokens = [item.split(" ") for item in item_info['context_plain']]
    context_plain_seqs, context_plain_lengths = merge(context_plain_tokens, is_context=True, plain=True)
    context_plain_seqs = [" ".join(context_plain) for context_plain in context_plain_seqs]
    y_seqs, y_lengths = merge_multi_response(item_info["generate_y"])
    gating_label = torch.tensor(item_info["gating_label"])
    turn_domain = torch.tensor(item_info["turn_domain"])

    # BERT features
    all_input_ids = None
    all_input_mask = None
    all_segment_ids = None
    all_sub_word_masks = None

    if args['encoder'] == 'BERT':
        story_plain = context_plain_seqs
        max_seq_length = max(src_lengths)
        # max_seq_length = 512
        features = convert_examples_to_features(story_plain, tokenizer=tokenizer, max_seq_length=max_seq_length)
        all_input_ids = torch.tensor([f.input_ids for f in features], dtype=torch.long)
        all_input_mask = torch.tensor([f.input_mask for f in features], dtype=torch.uint8)
        all_segment_ids = torch.tensor([f.segment_ids for f in features], dtype=torch.long)
        all_sub_word_masks = torch.tensor([f.sub_word_masks for f in features], dtype=torch.uint8)

    item_info["context"] = src_seqs
    item_info["context_plain"] = context_plain_seqs
    item_info["context_len"] = src_lengths
    item_info["gating_label"] = gating_label
    item_info["turn_domain"] = turn_domain
    item_info["generate_y"] = y_seqs
    item_info["y_lengths"] = y_lengths
    item_info['all_input_ids'] = all_input_ids
    item_info['all_input_mask'] = all_input_mask
    item_info['all_segment_ids'] = all_segment_ids
    item_info['all_sub_word_masks'] = all_sub_word_masks

    return item_info

def read_langs(file_name, gating_dict, SLOTS, dataset, lang, mem_lang, sequicity, training, max_line = None):
    print(("Reading from {}".format(file_name)))
    data = []
    max_resp_len, max_value_len = 0, 0
    domain_counter = {} 
    with open(file_name) as f:
        dials = json.load(f)
        # create vocab first 
        for dial_dict in dials:
            if (args["all_vocab"] or dataset=="train") and training:
                for ti, turn in enumerate(dial_dict["dialogue"]):
                    lang.index_words(turn["system_transcript"], 'utter')
                    lang.index_words(turn["transcript"], 'utter')
        # determine training data ratio, default is 100%
        if training and args["data_ratio"] != 100:
            random.Random(10).shuffle(dials)
            dials = dials[:max(int(len(dials)*0.01*args["data_ratio"]), 1)]
        
        cnt_lin = 1
        for dial_dict in dials:
            dialog_history = ""
            last_belief_dict = {}
            # Filtering and counting domains
            filter_domain = False
            for domain in dial_dict["domains"]:
                if domain not in EXPERIMENT_DOMAINS:
                    filter_domain = True
                    break
                if domain not in domain_counter.keys():
                    domain_counter[domain] = 0
                domain_counter[domain] += 1
            if filter_domain:
                continue

            all_domains = set(dial_dict['domains'])
            # add sometimes missing domains to annotation
            for turn in dial_dict['dialogue']:
                turn_belief_dict = fix_general_label_error(turn["belief_state"], False, SLOTS)
                for slot_key, slot_value in turn_belief_dict.items():
                    if slot_value == 'none':
                        continue
                    domain, slot_name = slot_key.split('-', maxsplit=1)
                    all_domains.add(domain)
            dial_dict['domains'] = list(all_domains)
            dial_dict['domains'].sort()

            # Unseen domain setting
            if args["only_domain"] != "" and args["only_domain"] not in dial_dict["domains"]:
                continue
            if args['except_domain_dev'] != '' and dataset == 'dev' and args['except_domain_dev'] in dial_dict['domains']:
                continue
            if (args["except_domain"] != "" and dataset == "test" and args["except_domain"] not in dial_dict["domains"]) or \
               (args["except_domain"] != "" and dataset != "test" and args["except_domain"] in dial_dict["domains"]):
                continue

            # Reading data
            for ti, turn in enumerate(dial_dict["dialogue"]):
                turn_domain = turn["domain"]
                turn_id = turn["turn_idx"]
                turn_uttr = turn["system_transcript"] + " ; " + turn["transcript"]
                turn_uttr_strip = turn_uttr.strip()
                dialog_history +=  (turn["system_transcript"] + " ; " + turn["transcript"] + " ; ")
                source_text = dialog_history.strip()
                turn_belief_dict = fix_general_label_error(turn["belief_state"], False, SLOTS)

                # Generate domain-dependent slot list
                slot_temp = SLOTS
                # if dataset == "train" or dataset == "dev":
                #     if args["except_domain"] != "":
                #         slot_temp = [k for k in SLOTS if args["except_domain"] not in k]
                #         turn_belief_dict = OrderedDict([(k, v) for k, v in turn_belief_dict.items() if args["except_domain"] not in k])
                #     elif args["only_domain"] != "":
                #         slot_temp = [k for k in SLOTS if args["only_domain"] in k]
                #         turn_belief_dict = OrderedDict([(k, v) for k, v in turn_belief_dict.items() if args["only_domain"] in k])
                # else:
                #     if args["except_domain"] != "":
                #         slot_temp = [k for k in SLOTS if args["except_domain"] in k]
                #         turn_belief_dict = OrderedDict([(k, v) for k, v in turn_belief_dict.items() if args["except_domain"] in k])
                #     elif args["only_domain"] != "":
                #         slot_temp = [k for k in SLOTS if args["only_domain"] in k]
                #         turn_belief_dict = OrderedDict([(k, v) for k, v in turn_belief_dict.items() if args["only_domain"] in k])

                turn_belief_list = [str(k)+'-'+str(v) for k, v in turn_belief_dict.items()]

                if (args["all_vocab"] or dataset=="train") and training:
                    mem_lang.index_words(turn_belief_dict, 'belief')

                class_label, generate_y, slot_mask, gating_label  = [], [], [], []
                start_ptr_label, end_ptr_label = [], []
                for slot in slot_temp:
                    if slot in turn_belief_dict.keys(): 
                        generate_y.append(turn_belief_dict[slot])

                        if turn_belief_dict[slot] == "dontcare":
                            gating_label.append(gating_dict["dontcare"])
                        elif turn_belief_dict[slot] == "none":
                            gating_label.append(gating_dict["none"])
                        else:
                            gating_label.append(gating_dict["ptr"])

                        if max_value_len < len(turn_belief_dict[slot]):
                            max_value_len = len(turn_belief_dict[slot])

                    else:
                        generate_y.append("none")
                        gating_label.append(gating_dict["none"])
                
                data_detail = {
                    "ID":dial_dict["dialogue_idx"], 
                    "domains":dial_dict["domains"], 
                    "turn_domain":turn_domain,
                    "turn_id":turn_id, 
                    "dialog_history":source_text, 
                    "turn_belief":turn_belief_list,
                    "gating_label":gating_label, 
                    "turn_uttr":turn_uttr_strip, 
                    'generate_y':generate_y
                    }
                data.append(data_detail)
                
                if max_resp_len < len(source_text.split()):
                    max_resp_len = len(source_text.split())
                
            cnt_lin += 1
            if(max_line and cnt_lin>=max_line):
                break

    # add t{} to the lang file
    if "t{}".format(max_value_len-1) not in mem_lang.word2index.keys() and training:
        for time_i in range(max_value_len):
            mem_lang.index_words("t{}".format(time_i), 'utter')

    print("domain_counter", domain_counter)
    return data, max_resp_len, slot_temp


def get_seq(pairs, lang, mem_lang, batch_size, type, sequicity, tokenizer=None):
    if(type and args['fisher_sample']>0):
        shuffle(pairs)
        pairs = pairs[:args['fisher_sample']]

    data_info = {}
    data_keys = pairs[0].keys()
    for k in data_keys:
        data_info[k] = []

    for pair in pairs:
        for k in data_keys:
            data_info[k].append(pair[k]) 

    dataset = Dataset(data_info, lang.word2index, lang.word2index, sequicity, mem_lang.word2index)

    if args["imbalance_sampler"] and type:
        data_loader = torch.utils.data.DataLoader(dataset=dataset,
                                                  batch_size=batch_size,
                                                  # shuffle=type,
                                                  collate_fn=lambda data: collate_fn(data, tokenizer),
                                                  sampler=ImbalancedDatasetSampler(dataset))
    else:
        data_loader = torch.utils.data.DataLoader(dataset=dataset,
                                                  batch_size=batch_size,
                                                  shuffle=type,
                                                  collate_fn=lambda data: collate_fn(data, tokenizer))
    return data_loader


def dump_pretrained_emb(word2index, index2word, dump_path):
    print("Dumping pretrained embeddings...")
    # import ssl
    # ssl._create_default_https_context = ssl._create_unverified_context
    embeddings = [GloveEmbedding(), KazumaCharEmbedding()]
    E = []
    for i in tqdm(range(len(word2index.keys()))):
        w = index2word[i]
        e = []
        for emb in embeddings:
            e += emb.emb(w, default='zero')
        E.append(e)
    with open(dump_path, 'wt') as f:
        json.dump(E, f)


def get_slot_information(ontology):
    ontology_domains = dict([(k, v) for k, v in ontology.items() if k.split("-")[0] in EXPERIMENT_DOMAINS])
    SLOTS = [k.replace(" ","").lower() if ("book" not in k) else k.lower() for k in ontology_domains.keys()]
    return SLOTS


def prepare_data_seq(training, task="dst", sequicity=0, batch_size=100):
    if args['encoder'] == 'BERT':
        tokenizer = BertTokenizer.from_pretrained(args['bert_model'], do_lower_case=args['do_lower_case'])
    else:
        tokenizer = None

    eval_batch = args["eval_batch"] if args["eval_batch"] else batch_size
    file_train = args['data_dir'] + '/train_dials.json'
    file_dev = args['data_dir'] + '/dev_dials.json'
    file_test = args['data_dir'] + '/test_dials.json'
    # Create saving folder
    if args['path']:
        folder_name = args['path'].rsplit('/', 2)[0] + '/'
    else:
        folder_name = 'save/{}-'.format(args["decoder"])+args["addName"]+args['dataset']+str(args['task'])+'/'
    print("folder_name", folder_name)
    if not os.path.exists(folder_name): 
        os.makedirs(folder_name)
    # load domain-slot pairs from ontology
    ontology = json.load(open(args['data_dir'] + "/multi-woz/MULTIWOZ2.1/ontology.json", 'r'))
    ALL_SLOTS = get_slot_information(ontology)
    gating_dict = {"ptr":0, "dontcare":1, "none":2}
    # Vocabulary
    lang, mem_lang = Lang(), Lang()
    lang.index_words(ALL_SLOTS, 'slot')
    mem_lang.index_words(ALL_SLOTS, 'slot')
    lang_name = 'lang-all.pkl' if args["all_vocab"] else 'lang-train.pkl'
    mem_lang_name = 'mem-lang-all.pkl' if args["all_vocab"] else 'mem-lang-train.pkl'


    if training:
        pair_train, train_max_len, slot_train = read_langs(file_train, gating_dict, ALL_SLOTS, "train", lang, mem_lang, sequicity, training)
        train = get_seq(pair_train, lang, mem_lang, batch_size, True, sequicity, tokenizer)
        nb_train_vocab = lang.n_words
        pair_dev, dev_max_len, slot_dev = read_langs(file_dev, gating_dict, ALL_SLOTS, "dev", lang, mem_lang, sequicity, training)
        dev   = get_seq(pair_dev, lang, mem_lang, eval_batch, False, sequicity, tokenizer)
        pair_test, test_max_len, slot_test = read_langs(file_test, gating_dict, ALL_SLOTS, "test", lang, mem_lang, sequicity, training)
        test  = get_seq(pair_test, lang, mem_lang, eval_batch, False, sequicity, tokenizer)
        if os.path.exists(folder_name+lang_name) and os.path.exists(folder_name+mem_lang_name):
            print("[Info] Loading saved lang files...")
            with open(folder_name+lang_name, 'rb') as handle: 
                lang = pickle.load(handle)
            with open(folder_name+mem_lang_name, 'rb') as handle: 
                mem_lang = pickle.load(handle)
        else:
            print("[Info] Dumping lang files...")
            with open(folder_name+lang_name, 'wb') as handle: 
                pickle.dump(lang, handle)
            with open(folder_name+mem_lang_name, 'wb') as handle: 
                pickle.dump(mem_lang, handle)
        emb_dump_path = args['data_dir'] + '/emb{}.json'.format(len(lang.index2word))
        if not os.path.exists(emb_dump_path) and args["load_embedding"]:
            dump_pretrained_emb(lang.word2index, lang.index2word, emb_dump_path)
    else:
        with open(folder_name+lang_name, 'rb') as handle:
            lang = pickle.load(handle)
        with open(folder_name+mem_lang_name, 'rb') as handle:
            mem_lang = pickle.load(handle)

        pair_train, train_max_len, slot_train, train, nb_train_vocab = [], 0, {}, [], 0
        pair_dev, dev_max_len, slot_dev = read_langs(file_dev, gating_dict, ALL_SLOTS, "dev", lang, mem_lang, sequicity, training)
        dev   = get_seq(pair_dev, lang, mem_lang, eval_batch, False, sequicity, tokenizer)
        pair_test, test_max_len, slot_test = read_langs(file_test, gating_dict, ALL_SLOTS, "test", lang, mem_lang, sequicity, training)
        test  = get_seq(pair_test, lang, mem_lang, eval_batch, False, sequicity, tokenizer)

    test_4d = []
    if args['except_domain']!="":
        pair_test_4d, _, _ = read_langs(file_test, gating_dict, ALL_SLOTS, "dev", lang, mem_lang, sequicity, training)
        test_4d  = get_seq(pair_test_4d, lang, mem_lang, eval_batch, False, sequicity, tokenizer)

    max_word = max(train_max_len, dev_max_len, test_max_len) + 1

    print("Read %s pairs train" % len(pair_train))
    print("Read %s pairs dev" % len(pair_dev))
    print("Read %s pairs test" % len(pair_test))  
    print("Vocab_size: %s " % lang.n_words)
    print("Vocab_size Training %s" % nb_train_vocab )
    print("Vocab_size Belief %s" % mem_lang.n_words )
    print("Max. length of dialog words for RNN: %s " % max_word)

    SLOTS_LIST = [ALL_SLOTS, slot_train, slot_dev, slot_test]
    print("[Train Set & Dev Set Slots]: Number is {} in total".format(str(len(SLOTS_LIST[2]))))
    print(SLOTS_LIST[2])
    print("[Test Set Slots]: Number is {} in total".format(str(len(SLOTS_LIST[3]))))
    print(SLOTS_LIST[3])
    LANG = [lang, mem_lang]
    return train, dev, test, test_4d, LANG, SLOTS_LIST, gating_dict, nb_train_vocab



class ImbalancedDatasetSampler(torch.utils.data.sampler.Sampler):
    """Samples elements randomly from a given list of indices for imbalanced dataset
    Arguments:
        indices (list, optional): a list of indices
        num_samples (int, optional): number of samples to draw
    """

    def __init__(self, dataset, indices=None, num_samples=None):
                
        # if indices is not provided, 
        # all elements in the dataset will be considered
        self.indices = list(range(len(dataset))) \
            if indices is None else indices
            
        # if num_samples is not provided, 
        # draw `len(indices)` samples in each iteration
        self.num_samples = len(self.indices) \
            if num_samples is None else num_samples
            
        # distribution of classes in the dataset 
        label_to_count = {}
        for idx in self.indices:
            label = self._get_label(dataset, idx)
            if label in label_to_count:
                label_to_count[label] += 1
            else:
                label_to_count[label] = 1
                
        # weight for each sample
        weights = [1.0 / label_to_count[self._get_label(dataset, idx)] for idx in self.indices]
        self.weights = torch.DoubleTensor(weights)

    def _get_label(self, dataset, idx):
        return dataset.turn_domain[idx]
                
    def __iter__(self):
        return (self.indices[i] for i in torch.multinomial(self.weights, self.num_samples, replacement=True))

    def __len__(self):
        return self.num_samples
