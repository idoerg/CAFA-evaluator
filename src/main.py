from graph import top_sort, propagate
from parser import parse_obo, gt_parser, split_pred_parser, parse_ia_dict
from evaluation import get_toi_idx, get_leafs_idx, get_roots_idx, compute_metrics, compute_f, compute_s, plot_pr_rc, plot_mi_ru
import argparse
import logging
import os
import numpy as np
import pandas as pd


namespaces = {"bpo": "biological_process", "cco": "cellular_component", "mfo": "molecular_function",
 "disorderfunction": "Disorder_function", "interactionpartner": "Interaction_partner",
 "structuralstate": "Structural_state", "structuraltransition": "Structural_transition"}

parser = argparse.ArgumentParser(description='CAFA-evaluator. Calculate precision-recall plots and F-max')
parser.add_argument('obo_file', help='Ontology file in OBO format')
parser.add_argument('pred_dir', help='Predictions directory. Sub-folders are iterated recursively')
parser.add_argument('gt_dir', help='Ground truth directory. Sub-folders are iterated recursively')
parser.add_argument('-out_dir', help='Output directory. Default to \"results\"', default='results')
parser.add_argument('-names', help='File with methods information (filename, group, label, is_baseline)')
parser.add_argument('-ia', help='File with information accretion (term, information_accretion)')
args = parser.parse_args()

tau_arr = np.arange(0.01, 1, 0.01)  # array of tau, used to compute precision and recall at different score threshold
obo_file = args.obo_file
pred_folder = os.path.normpath(args.pred_dir) + "/"  # add the tailing "/"
gt_folder = os.path.normpath(args.gt_dir) + "/"
out_folder = os.path.normpath(args.out_dir) + "/"
ia_file = args.ia

if not os.path.isdir(out_folder):
    os.mkdir(out_folder)

# Set the logger
logFormatter = logging.Formatter("%(asctime)s [%(levelname)-5.5s] %(message)s")
rootLogger = logging.getLogger()
rootLogger.setLevel(logging.INFO)

fileHandler = logging.FileHandler("{0}/info.log".format(out_folder))
fileHandler.setFormatter(logFormatter)
rootLogger.addHandler(fileHandler)

consoleHandler = logging.StreamHandler()
consoleHandler.setFormatter(logFormatter)
rootLogger.addHandler(consoleHandler)

# parsing the ontology and for each namespace in the obo file creates a different graph structure
ontologies = parse_obo(obo_file)

# Parse and set information accretion
ia_dict = None
if ia_file is not None:
    ia_dict = parse_ia_dict(ia_file)
    for ont in ontologies:
        ont.set_ia_array(ia_dict)

# Set gt files
gt_files = []
for root, dirs, files in os.walk(gt_folder):
    for file in files:
        gt_files.append(os.path.join(root, file))
logging.debug("Gt files {}".format(gt_files))

# Set prediction files
pred_files = []
for root, dirs, files in os.walk(pred_folder):
    for file in files:
        pred_files.append(os.path.join(root, file))
logging.debug("Prediction paths {}".format(pred_files))

# Set method information
methods = None
if args.names:
    methods = pd.read_csv(args.names, delim_whitespace=True)
    logging.debug(methods)
logging.debug("Methods {}".format(methods))

# for each namespace sort term, and identify roots, leaves and used terms
order = {}
toi = {}
for ont in ontologies:
    ns = ont.get_namespace()
    roots = get_roots_idx(ont.dag)
    leafs = get_leafs_idx(ont.dag)
    order[ns] = top_sort(ont)
    toi[ns] = get_toi_idx(ont.dag)
    logging.info("{} {} roots, {} leaves".format(ns, len(roots), len(leafs)))

# find the right ontology for each benchmark(filter) file
gt_paths = {}
predictions = {}
ne = {}
for k, v in namespaces.items():
    for gt in gt_files:
        gt_paths.setdefault(v)
        if k.lower() in gt.lower():
            gt_paths[v] = gt
logging.debug("Gt paths {}".format(gt_paths))

# if a ground truth for a specific namespace is missing the namespace is ignored
unused_onts = []
for i in range(0, len(ontologies)):
    ns = ontologies[i].get_namespace()
    if gt_paths.get(ns) is None:
        unused_onts.append(i)
unused_onts.reverse()
for idx in unused_onts:
    ontologies.pop(idx)

if len(ontologies) == 0:
    raise ValueError("Ground truth filenames not matching any OBO namespace")

# parsing the ground truth for each remaining namespace
gts = {}
with open(out_folder + "/gt_stat.tsv", "wt") as out_file:
    out_file.write("namespace\texpanded\tdirect\tterm\tname\n")
    for ont in ontologies:
        ns = ont.get_namespace()
        if gt_paths.get(ns) is not None:

            gts[ns] = gt_parser(gt_paths[ns], ont.go_terms)

            stat_pre = gts[ns].matrix.sum(axis=0)

            _ = propagate(gts[ns], ont, order[ns])

            ne[ns] = gts[ns].matrix.shape[0]

            logging.info("Ground truth {} targets {} {} {}".format(ns, len(gts[ns].ids), ne[ns], gts[ns].matrix.any(axis=1).sum()))

            stat_post = gts[ns].matrix.sum(axis=0)

            for stat_post, stat_pre, c in sorted(zip(stat_post, stat_pre, gts[ns].terms), reverse=True):
                out_file.write("{}\t{}\t{}\t{}\t{}\n".format(ns, stat_post - stat_pre, stat_pre, c[1], c[2]))


# parses each prediction file, removing the proteins not contained in the ground truth
# computes precision and recall sums that will be divided only at the end of the cycle
pr_sum = {}
rc_sum = {}
mi_sum = {}
ru_sum = {}

data = {'ns': [], 'method': [], 'tau': [], 'pr': [], 'rc': [], 'f': [], 'cov_f': [], 'mi': [], 'ru': [], 's': [], 'cov_s': []}
for file_name in pred_files:
    method = file_name.replace(pred_folder, '').replace('/', '_')

    for ont in ontologies:
        ns = ont.get_namespace()
        pr_sum[ns] = np.zeros((len(tau_arr), 2), dtype='float')
        rc_sum[ns] = np.zeros(len(tau_arr), dtype='float')
        ru_sum[ns] = np.zeros((len(tau_arr), 2), dtype='float')
        mi_sum[ns] = np.zeros((len(tau_arr), 2), dtype='float')

    predictions = split_pred_parser(file_name, ontologies, gts)
    for p in predictions:
        ns = p.namespace
        for o in ontologies:
            if o.get_namespace() == ns:
                ont = o
        _ = propagate(p, ont, order[ns])

        pr, rc, ru, mi = compute_metrics(p, gts[ns], tau_arr, toi[ns], ont.ia_array)
        pr_sum[ns] += pr
        rc_sum[ns] += rc

        ru_sum[ns] += ru
        mi_sum[ns] += mi

    # computing the actual value of precision and recall for each threshold
    for ont in ontologies:
        ns = ont.get_namespace()

        data['ns'].extend([ns] * len(tau_arr))
        data['method'].extend([method] * len(tau_arr))
        data['tau'].extend(tau_arr)

        n = pr_sum[ns][:, 0]
        d = pr_sum[ns][:, 1]
        _pr = np.divide(n, d, out=np.zeros_like(n, dtype='float'), where=d != 0)
        _rc = rc_sum[ns] / ne[ns]
        data['pr'].extend(_pr)
        data['rc'].extend(_rc)
        data['f'].extend(compute_f(_pr, _rc))
        data['cov_f'].extend(d / ne[ns])

        # ru[ns] = ru_sum[ns] / ne[ns]
        # mi[ns] = mi_sum[ns] / ne[ns]

        n = ru_sum[ns][:, 0]
        d = ru_sum[ns][:, 1]
        _ru = np.divide(n, d, out=np.zeros_like(n, dtype='float'), where=d != 0)

        n = mi_sum[ns][:, 0]
        d = mi_sum[ns][:, 1]
        _mi = np.divide(n, d, out=np.zeros_like(n, dtype='float'), where=d != 0)
        data['ru'].extend(_ru)
        data['mi'].extend(_mi)
        data['s'].extend(compute_s(_ru, _mi))
        data['cov_s'].extend(d / ne[ns])

# add names and groups
data = pd.DataFrame(data)
data = pd.merge(data, methods, left_on='method', right_on='name', how='left')
data['group'].fillna(data['method'], inplace=True)
data['label'].fillna(data['method'], inplace=True)
data['is_baseline'].fillna(False, inplace=True)
data.set_index(['ns', 'group', 'method'], inplace=True)

plot_pr_rc(data, out_folder)
plot_mi_ru(data, out_folder)



