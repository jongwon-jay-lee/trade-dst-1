import numpy as np
import json
import os
import re
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
import ast
usr = os.path.expanduser('~')

    
def difference(new, old):
    new_set = set(new)
    old_set = set(old)
    added = list(new_set-old_set)
    return added

def compare_lists(k, listA, listB):
    print(listA, listB)
    if set(listA) == set(listB):
        return True
    else:
        return False

def read_csv(path):
    with open(path) as f:
        lines = [el.strip() for el in f.readlines()]
        return lines

def read_json(path):
    with open(path) as f:
        json_object = json.load(f)
    return json_object

def json_to_frame(json_object):
    return pd.read_json(json.dumps(json_object)).transpose()
    
def n_conversations(results):
    return len(results.keys())

def n_turns(results):
    return sum([len(results[el].keys()) for el in results.keys()])

all_slots = read_csv(os.path.join(usr,"data/trade/all_slots.txt"))

# '''
# This checks for full <slot type - value> accuracy, doesn't split it...
# '''
# def add_error_types(frame):
    
#     pred_step_belief = frame['pred_step_belief']
#     true_step_belief = frame['true_step_belief']
    
#     pred_full_belief = frame['pred_full_belief']
#     true_full_belief = frame['true_full_belief']


#     frame["det_inserted"] = list(set(pred_step_belief) - set(true_step_belief))
#     frame["det_missed"] = list(set(true_step_belief) - set(pred_step_belief))
#     frame["det_full_correct"] = [el in pred_full_belief for el in true_full_belief]
#     frame["det_step_correct"] = [el in pred_step_belief for el in true_step_belief]
    
#     det_step_correct = frame["det_step_correct"]
#     det_full_correct = frame["det_full_correct"]
#     det_inserted = frame["det_inserted"]
#     det_missed = frame["det_missed"]
    
#     frame["step_correct"] = (False not in det_step_correct)#bool(sum(det_step_correct))
#     frame["full_correct"] = (False not in det_full_correct) #bool(sum(det_full_correct))  
#     frame["inserted"] = (len(det_inserted)>0)
#     frame["missed"] = (len(det_missed)>0)
#     if len(det_full_correct) > 0:
#         frame["percent_found"] = sum(det_full_correct)/len(det_full_correct)
#     else:
#         frame["percent_found"] = None
    
#     return frame

def select_dialogue(frame, dial_id):
    return df[df["dialogue"]==dial_id]

def find_turns_with_text(frame, text):
    bool_frame = frame[["system_transcript", "transcript"]].apply(lambda x: x.str.contains(text, regex=False))
    select = frame[["system_transcript", "transcript"]][np.logical_or(bool_frame.system_transcript, bool_frame.transcript)]
    return select

# '''
# Input: json file generated by TRADE inference.
# Output: a pandas dataframe where each row corresponds to a unique turn in a given dialogue
# (indexed as <dialogue>_<turn>), with the following features:
# - predicted_belief: the full (turn 1 - now) predicted set of slot-value pairs
# - true_belief: the full (turn 1 - now) correct set of slot-value pairs
# '''
# def generate_turn_frame(predictions, pred_slot_columns=False, gt_slot_columns=False):
#     slot_updates = dict()
#     for d_idx, dialogue in predictions.items():
#         old_belief = []
#         true_old_belief = []
#         for t_idx in range(len(dialogue.keys())):
#             # the unique id: <dialogue>_<turn>
#             idx = '_'.join((d_idx.split('.')[0], str(t_idx+1)))
#             t_idx = str(t_idx)
#             slot_updates[idx] = dict()
#             turn = dialogue[t_idx]
#             new_belief = turn['pred_bs_ptr']
#             true_belief = turn['turn_belief']
#             added_belief = difference(new_belief, old_belief)
#             true_added_belief = difference(true_belief, true_old_belief)
            
#             # has anything been added?
#             added_empty = (len(added_belief) == 0)
#             true_empty = (len(true_added_belief) == 0)
            
#             slot_updates[idx]['dialogue'] = d_idx.split('.')[0]
#             slot_updates[idx]['turn'] = t_idx
            
#             slot_updates[idx]['pred_full_belief'] = new_belief
#             slot_updates[idx]['true_full_belief'] = true_belief
#             slot_updates[idx]['pred_step_belief'] = added_belief 
#             slot_updates[idx]['true_step_belief'] = true_added_belief
            
#             slot_updates[idx] = add_error_types(slot_updates[idx])
            
#             slot_updates[idx]['pred_empty'] = added_empty
#             slot_updates[idx]['true_empty'] = true_empty
            
#             old_belief = new_belief
#             true_old_belief = true_belief
            
#     slot_updates = pd.read_json(json.dumps(slot_updates, indent=4)).transpose()
#     return slot_updates

def calculate_errors(merged):
    missed = (merged["missed"] != False)
    added = (merged["inserted"] != False)
    empty = (merged["pred_empty"] == True)
    swaps = np.logical_and(missed, added).sum()
    n_turns = merged.shape[0]
    n_steps_correct = (merged["step_correct"] == True).sum()
    n_steps_wrong = (merged["step_correct"] == False).sum()
    n_swaps = swaps.sum()
    n_missed = missed.sum()-n_swaps
    n_added = added.sum()-n_swaps
    n_empty_correct = np.logical_and(merged["pred_empty"], merged["true_empty"]).sum()
    step_acc = n_steps_correct/n_turns
    joint_acc = (merged["full_correct"] == True).sum()/n_turns
    step_swap = n_swaps/n_steps_wrong
    step_delete = n_missed/n_steps_wrong
    step_insert = n_added/n_steps_wrong
    return step_acc, joint_acc, step_swap, step_delete, step_insert, n_empty_correct/n_steps_correct

#def add_slot_vectors():
    

def generate_slot_plot(data, all_slots, experiment, string, figsize=(12,8)):
    appears = []
    correct = []
    
    ### Need to do this on a row level
    added_slots = ['-'.join(el.split('-')[:2]) for el in data["true_step_belief"]]
    # added_slots in a binary vector - we want a binary value for each slot
    added_slots_vector = [True if el in added_slots else False for el in all_slots]
    data.update(dict(zip(all_slots, added_slots_vector)))

    for slot in all_slots:
        #contains_slot = merged[merged[slot+"_y"] == True]
        contains_slot = data[data[slot] == True]
        number_correct = contains_slot['step_correct'].sum()
        slot_occurences = contains_slot.shape[0]
        appears.append(slot_occurences)
        correct.append(number_correct)

    columns = ["slots", "frequency of slot", "slot inferred correctly"]
    test_data = pd.DataFrame.from_records(list(zip(all_slots, appears, correct)),
                                           columns=columns)
    test_data_melted = pd.melt(test_data, id_vars=columns[0],\
                               var_name="source", value_name="counts")
    fig, ax1 = plt.subplots(figsize=figsize)
    g = sns.barplot(x=columns[0], y="counts", hue="source",\
                    data=test_data_melted, ax=ax1)

    plt.xticks(rotation=90)
    ax2 = ax1.twinx()
    ax2.set_ylim(ax1.get_ylim())
    plt.title(experiment)
    plt.text(10,1000, string, fontsize=14)
    plt.show()
    plt.tight_layout()
    p = fig.savefig(fname = os.path.join(usr, ''.join(("misc_trade/results/", experiment, "/slot_distribution.jpg"))))
    
    
def experiment_path(experiment):
    return os.path.join(usr,''.join(("misc_trade/results/", experiment)))

# def experiment_results_frame(input_file):
#     output_file = os.path.join(experiment_path(experiment), "inference_turn_info.csv")
#     baseline_test_set = read_json(input_file)
#     frame = generate_turn_frame(baseline_test_set)
#     return frame

# def get_errors(df):
#     df_correct = df["det_full_correct"].apply(sum).astype(float)
#     df_slots = df["det_full_correct"].apply(len).astype(float)
#     df["percent_correct"] = df_correct/df_slots
#     a = df[["turn", "percent_correct"]]
#     partially_correct = a[(a["percent_correct"]>0) & (a["percent_correct"] < 1)]
#     fully_correct = a[a["percent_correct"] == 1]
#     fully_incorrect = a[a["percent_correct"] == 0]
#     correct_empty = a[a["percent_correct"].isna()]
#     return partially_correct, fully_correct, fully_incorrect, correct_empty

def plot_error_histograms(df, experiment):
    partially_correct, fully_correct, fully_incorrect, correct_empty = get_errors(df)
    plt.hist(fully_correct.turn.values.astype(int), alpha=0.2, label='correct', histtype='stepfilled')
    plt.hist(partially_correct.turn.values.astype(int), alpha=0.3, label='partial', histtype='stepfilled')
    plt.hist(correct_empty.turn.values.astype(int), alpha=0.7, label='correct empty', histtype='bar', rwidth=2)
    plt.hist(fully_incorrect.turn.values.astype(int), alpha=0.5, label='fully incorrect', histtype='stepfilled')
    plt.xticks(list(range(max_turn_len)))
    #plt.yscale('log')
    plt.legend(loc='upper right')
    plt.xlabel("example length")
    plt.ylabel("number of examples")
    plt.title(experiment)
    plt.show()
    
# TODO: make axes sizes consistent
def plot_empty_beliefs(df, experiment):
    plt.hist(df.turn.values.astype(int), alpha=0.7, label='all turns', histtype='stepfilled')
    plt.hist(df[df.true_full_belief.apply(len)==0].turn.values.astype(int), alpha=1, label='empty belief', histtype='stepfilled')
    plt.legend(loc='upper right')
    plt.xlabel("example length")
    plt.ylabel("number of examples")
    plt.title(experiment)
    plt.show()
   