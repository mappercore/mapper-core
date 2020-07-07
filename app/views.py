from flask import render_template,request, url_for, jsonify, redirect, Response, send_from_directory
from app import app
from app import APP_STATIC
from app import APP_ROOT
import json
import numpy as np
import pandas as pd
import os
import re
# from kmapper import KeplerMapper, Cover
from .kmapper import KeplerMapper, Cover
from sklearn import cluster
import networkx as nx
import sklearn
# from sklearn.linear_model import LinearRegression
import statsmodels.api as sm
from sklearn.decomposition import PCA
from sklearn.preprocessing import MinMaxScaler
from sklearn.neighbors import KernelDensity
from scipy.spatial import distance

@app.route('/')
@app.route('/MapperInteractive_new')
def index():
    return render_template('index.html')

@app.route('/data_process', methods=['POST','GET'])
def process_text_data():
    '''
    Check for:
    1. Missing value
    2. Non-numerical elements in numerical cols
    3. If cols are non-numerical, check if cols are categorical
    '''
    text_data = request.get_data().decode('utf-8').splitlines()
    print(len(text_data))
    cols = text_data[0].split(',')
    mat = [n.split(',') for n in text_data] # csv: if an element is empty, it will be "".
    newdf1 = np.array(mat)[1:]
    rows2delete = np.array([])
    cols2delete = []
    
    ### Delete missing values ###
    for i in range(len(cols)):
        col = newdf1[:,i]
        if np.sum(col == "") >= 0.2*len(newdf1): # if less than 80% elements in this column are numerical, delete the whole column
            cols2delete.append(i)
        else:
            rows2delete = np.concatenate((rows2delete, np.where(col=="")[0]))
    rows2delete = np.unique(rows2delete).astype("int")
    newdf2 = np.delete(np.delete(newdf1, cols2delete, axis=1), rows2delete, axis=0)
    cols = [cols[i] for i in range(len(cols)) if i not in cols2delete]

    ### check if numerical cols ###
    cols_numerical_idx = []
    cols_categorical_idx = []
    rows2delete = np.array([])
    r1 = re.compile(r'^-?\d+(?:\.\d+)?$')
    r2 = re.compile(r'[+\-]?[^A-Za-z]?(?:0|[1-9]\d*)(?:\.\d*)?(?:[eE][+\-]?\d+)') # scientific notation
    vmatch = np.vectorize(lambda x:bool(r1.match(x) or r2.match(x)))
    for i in range(len(cols)):
        col = newdf2[:,i]
        col_match = vmatch(col)
        if np.sum(col_match) >= 0.8*len(newdf1): # if more than 90% elements can be converted to float, keep the col, and delete rows that cannot be convert to float:
            cols_numerical_idx.append(i)
            rows2delete = np.concatenate((rows2delete, np.where(col_match==False)[0]))
        else: 
            ### check if categorical cols### 
            if len(np.unique(col)) <= 10: # if less than 10 different values: categorical
                cols_categorical_idx.append(i)
    newdf3 = newdf2[:, cols_numerical_idx+cols_categorical_idx]
    newdf3 = np.delete(newdf3, rows2delete, axis=0)
    newdf3_cols = [cols[idx] for idx in cols_numerical_idx+cols_categorical_idx]
    newdf3 = pd.DataFrame(newdf3)
    newdf3.columns = newdf3_cols
    # write the data frame
    newdf3.to_csv(APP_STATIC+"/uploads/processed_data.csv", index=False) 
    # write the cols info
    cols_numerical = [cols[idx] for idx in cols_numerical_idx]
    cols_categorical = [cols[idx] for idx in cols_categorical_idx]
    cols_dict = {'cols_numerical':cols_numerical, 'cols_categorical':cols_categorical}
    with open(APP_STATIC+"/uploads/cols_info.json", 'w') as f:
        f.write(json.dumps(cols_dict, indent=4))
    return jsonify(columns=cols_numerical)

@app.route('/mapper_loader', methods=['POST','GET'])
def get_graph():
    mapper_data = request.form.get('data')
    mapper_data = json.loads(mapper_data)
    selected_cols = mapper_data['cols']
    data = pd.read_csv(APP_STATIC+"/uploads/processed_data.csv")
    data = data[selected_cols].astype("float")
    all_cols = list(data.columns)
    config = mapper_data["config"]
    norm_type = config["norm_type"]
    eps = config["eps"]
    min_samples = config["min_samples"]
    # filter functions
    filter_function = config["filter"]
    if len(filter_function) == 1:
        interval = int(config["interval1"])
        overlap = float(config["overlap1"]) / 100
    elif len(filter_function) == 2:
        interval = [int(config["interval1"]), int(config["interval2"])]
        overlap = [float(config["overlap1"])/100, float(config["overlap2"])/100]
    print(interval, overlap)
    # normalization
    if norm_type == "none":
        pass
    elif norm_type == "0-1": # axis=0, min-max norm for each column
        scaler = MinMaxScaler()
        # scaler.fit(data)
        data = scaler.fit_transform(data)
    else:
        data = sklearn.preprocessing.normalize(data, norm=norm_type, axis=0, copy=False, return_norm=False)
    data = pd.DataFrame(data, columns = all_cols)
    mapper_result = run_mapper(data, selected_cols, interval, overlap, eps, min_samples, filter_function)
    connected_components = compute_cc(mapper_result)
    return jsonify(mapper=mapper_result, connected_components=connected_components)

@app.route('/linear_regression', methods=['POST','GET'])
def linear_regression():
    json_data = json.loads(request.form.get('data'))
    selected_nodes = json_data['nodes']
    y_name = json_data['dep_var']
    X_names = json_data['indep_vars']
    print(y_name, X_names)
    with open(APP_STATIC+"/uploads/nodes_detail.json") as f:
        nodes_detail = json.load(f)
    data = pd.read_csv(APP_STATIC+"/uploads/processed_data.csv")
    selected_rows = []
    for node in selected_nodes:
        selected_rows += nodes_detail[node]
    selected_data = data.iloc[selected_rows,:]
    y = selected_data.loc[:,y_name]
    X = selected_data.loc[:,X_names]
    X2 = sm.add_constant(X)
    reg = sm.OLS(y, X2)
    result = reg.fit()
    conf_int = np.array(result.conf_int())
    conf_int_new = []
    for i in range(conf_int.shape[0]):
        conf_int_new.append(list(conf_int[i,:]))
    return jsonify(params=list(result.params), pvalues=list(result.pvalues), conf_int=conf_int_new, stderr=list(result.bse))

@app.route('/pca', methods=['POST','GET'])
def pca():
    '''
    Dimension reduction using PCA
    n_components = 2
    '''
    selected_nodes = json.loads(request.form.get('data'))['nodes']
    # print(selected_nodes)
    data = pd.read_csv(APP_STATIC+"/uploads/processed_data.csv")
    cols = data.columns
    # print(cols)
    with open(APP_STATIC+"/uploads/nodes_detail.json") as f:
        nodes_detail = json.load(f)
    node_labels = np.repeat(-999, data.shape[0])
    for node in selected_nodes:
        node_labels[nodes_detail[node]] = node
    data['node_label'] = node_labels
    data = data.iloc[np.where(data['node_label']!=-999)[0],:] # only selected rows
    # print(data)
    pca = PCA(n_components=2)
    pca.fit(data.loc[:,cols])
    data_new = pca.transform(data.loc[:,cols])
    data_new = pd.DataFrame(data_new)
    # data_new.columns = cols
    data_new['node_label'] = data['node_label']
    data_new2 = ''
    for i in range(data_new.shape[0]):
        data_new2+=str(list(data_new.iloc[i,:]))+'n'
    # print(data_new2)
    return jsonify(pca=data_new2)

def run_mapper(data_array, col_names, interval, overlap, dbscan_eps, dbscan_min_samples, filter_function):
        """This function is called when the form is submitted. It triggers construction of Mapper. 

        Each parameter of this function is defined in the configuration.

        To customize the Mapper construction, you can inherit from :code:`KeplerMapperConfig` and customize this function.


        Parameters
        -------------

        interval: int
            Number of intervals 

        overlap: float
            Percentage of overlap. This value will be divided by 100 to produce proporition.
        
        dbscan_eps: float
            :code:`eps` parameter for the DBSCAN clustering used in Kepler Mapper construction.
        
        dbscan_min_samples: int
            :code:`min_samples` parameter for the DBSCAN clustering used in Kepler Mapper construction.

        filter_function: str
            Projection for constructing the lens for Kepler Mapper.

        """
        # data_array = np.array(data_array)

        km_result = _call_kmapper(data_array, col_names, 
            interval,
            overlap,
            float(dbscan_eps),
            float(dbscan_min_samples),
            filter_function
        )
        return _parse_result(data_array, km_result)

def _call_kmapper(data, col_names, interval, overlap, eps, min_samples, filter_function):
    mapper = KeplerMapper()
    if len(col_names) == 1:
        data_new = np.array(data[col_names[0]]).reshape(-1,1)
    else:
        data_new = np.array(data[col_names])

    if len(filter_function) == 1:
        f = filter_function[0]
        if f in ["sum", "mean", "median", "max", "min", "std", "l2norm"]:
            lens = mapper.fit_transform(data_new, projection=f)
        elif f == "Density":
            ### TODO: Allow users to select kernel and bandwidth ###
            kde = KernelDensity(kernel='gaussian', bandwidth=0.1).fit(data_new)
            lens = kde.score_samples(data_new).reshape(-1,1)
            scaler = MinMaxScaler()
            lens = scaler.fit_transform(lens)
        elif f == "Eccentricity":
            ### TODO: Allow users to select p and distance_matrix ###
            p = 0.5
            distance_matrix = "euclidean"
            pdist = distance.squareform(distance.pdist(data_new, metric=distance_matrix))
            lens = np.array([(np.sum(pdist**p, axis=1)/len(data_new))**(1/p)]).reshape(-1,1)
        elif f == "PC1":
            pca = PCA(n_components=2)
            lens = pca.fit_transform(data_new)[:,0]
            print("PCA")
            print(data_new.shape)
            print(pca.fit_transform(data_new))
        else:
            lens = np.array(data[f]).reshape(-1,1)
    elif len(filter_function) == 2:
        lens = []
        for f in filter_function:
            if f in ["sum", "mean", "median", "max", "min", "std", "l2norm"]:
                lens.append(mapper.fit_transform(data_new, projection=f))
            else:
                lens.append(np.array(data[f]).reshape(-1,1))
        lens = np.concatenate((lens[0], lens[1]), axis=1)

    # print(np.max(data_new, axis=0), np.min(data_new, axis=0))
    # print(np.max(lens, axis=0), np.min(lens, axis=0))
    graph = mapper.map_parallel(lens, data_new, clusterer=cluster.DBSCAN(eps=eps, min_samples=min_samples), cover=Cover(n_cubes=interval, perc_overlap=overlap))

    return graph


def _parse_result(data_array, graph):
    col_names = data_array.columns
    data_array = np.array(data_array)
    data = {"nodes": [], "links": []}

    # nodes
    node_keys = graph['nodes'].keys()
    name2id = {}
    i = 1
    nodes_detail = {}
    for key in node_keys:
        name2id[key] = i
        cluster = graph['nodes'][key]
        nodes_detail[i] = cluster
        cluster_data = data_array[cluster]
        cluster_avg = np.mean(cluster_data, axis=0)
        cluster_avg_dict = {}
        for j in range(len(col_names)):
            cluster_avg_dict[col_names[j]] = cluster_avg[j]
        data['nodes'].append({
            "id": str(i),
            "size": len(graph['nodes'][key]),
            "avgs": cluster_avg_dict,
            "vertices": cluster
            })
        i += 1
    
    with open(APP_STATIC+"/uploads/nodes_detail.json","w") as f:
        json.dump(nodes_detail, f)

    # links
    links = set()
    for link_from in graph['links'].keys():
        for link_to in graph['links'][link_from]:
            from_id = name2id[link_from]
            to_id = name2id[link_to]
            left_id = min(from_id, to_id)
            right_id = max(from_id, to_id)
            links.add((left_id, right_id))
    for link in links:
        data['links'].append({"source": link[0], "target": link[1]})
    return data

def compute_cc(graph): 
    '''
    Compute connected components for the mapper graph
    '''
    G = nx.Graph()
    for node in graph['nodes']:
        nodeId = int(node['id'])-1
        G.add_node(nodeId)
    for edge in graph['links']:
        sourceId = int(edge['source'])-1
        targetId = int(edge['target'])-1
        G.add_edge(sourceId, targetId)
    cc = nx.connected_components(G)
    cc_list = []
    for c in cc:
        cc_list.append(list(c))
    return cc_list