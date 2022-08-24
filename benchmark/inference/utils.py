import os.path as osp

from hetero_gat import HeteroGAT
from hetero_sage import HeteroGraphSAGE
from ogb.nodeproppred import PygNodePropPredDataset

import torch_geometric.transforms as T
from torch_geometric.datasets import OGB_MAG, Reddit
from torch_geometric.nn import GAT, GCN, PNA, EdgeCNN, GraphSAGE

models_dict = {
    'edge_cnn': EdgeCNN,
    'gat': GAT,
    'gcn': GCN,
    'pna': PNA,
    'sage': GraphSAGE,
    'rgat': HeteroGAT,
    'rgcn': HeteroGraphSAGE,
}


def get_dataset(name, root, use_sparse_tensor):
    path = osp.join(osp.dirname(osp.realpath(__file__)), root, name)
    transform = T.ToSparseTensor() if use_sparse_tensor else None
    if name == 'ogbn-mag':
        if transform is None:
            transform = T.ToUndirected(merge=True)
        else:
            transform = T.Compose([T.ToUndirected(merge=True), transform])
        dataset = OGB_MAG(root=path, preprocess='metapath2vec',
                          transform=transform)
    elif name == 'ogbn-products':
        dataset = PygNodePropPredDataset('ogbn-products', root=path,
                                         transform=transform)
    elif name == 'Reddit':
        dataset = Reddit(root=path, transform=transform)

    return dataset[0], dataset.num_classes


def get_model(name, params, metadata=None):
    Model = models_dict.get(name, None)
    assert Model is not None, f'Model {name} not supported!'

    if name == 'rgat':
        return Model(metadata, params['hidden_channels'], params['num_layers'],
                     params['output_channels'], params['num_heads'])

    if name == 'rgcn':
        return Model(metadata, params['hidden_channels'], params['num_layers'],
                     params['output_channels'])

    if name == 'gat':
        return Model(params['inputs_channels'], params['hidden_channels'],
                     params['num_layers'], params['output_channels'],
                     heads=params['num_heads'])

    if name == 'pna':
        return Model(params['inputs_channels'], params['hidden_channels'],
                     params['num_layers'], params['output_channels'],
                     aggregators=['mean', 'min', 'max', 'std'],
                     scalers=['identity', 'amplification',
                              'attenuation'], deg=params['degree'])

    return Model(params['inputs_channels'], params['hidden_channels'],
                 params['num_layers'], params['output_channels'])
