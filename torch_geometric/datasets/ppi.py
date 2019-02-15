import os
import os.path as osp
import json

import torch
import numpy as np
from torch_geometric.utils import remove_self_loops, to_undirected
from torch_geometric.data import (InMemoryDataset, Data, download_url,
                                  extract_zip)


class PPI(InMemoryDataset):
    r"""Protein-protein interaction networks from the `"Predicting
    Multicellular Function through Multi-layer Tissue Networks"
    <https://arxiv.org/abs/1707.04638>`_ paper, containing positional gene
    sets, motif gene sets and immunological signatures as features (50 in
    total) and gene ontology sets as labels (121 in total).
    Training, validation and test splits are given by binary masks.

    Args:
        root (string): Root directory where the dataset should be saved.
        transform (callable, optional): A function/transform that takes in an
            :obj:`torch_geometric.data.Data` object and returns a transformed
            version. The data object will be transformed before every access.
            (default: :obj:`None`)
        pre_transform (callable, optional): A function/transform that takes in
            an :obj:`torch_geometric.data.Data` object and returns a
            transformed version. The data object will be transformed before
            being saved to disk. (default: :obj:`None`)
    """

    url = 'http://snap.stanford.edu/graphsage/ppi.zip'

    def __init__(self, root, transform=None, pre_transform=None):
        super(PPI, self).__init__(root, transform, pre_transform)
        self.data, self.slices = torch.load(self.processed_paths[0])

    @property
    def raw_file_names(self):
        prefix = self.__class__.__name__.lower()
        suffix = ['G.json', 'feats.npy', 'class_map.json']
        return ['{}-{}'.format(prefix, s) for s in suffix]

    @property
    def processed_file_names(self):
        return 'data.pt'

    def download(self):
        path = download_url(self.url, self.root)
        extract_zip(path, self.root)
        os.unlink(path)
        name = self.__class__.__name__.lower()
        os.rename(osp.join(self.root, name), self.raw_dir)

    def process(self):
        x = torch.from_numpy(np.load(self.raw_paths[1])).float()

        with open(self.raw_paths[0], 'r') as f:
            graph_data = json.load(f)

        mask = torch.zeros(len(graph_data['nodes']), dtype=torch.uint8)
        for i in graph_data['nodes']:
            mask[i['id']] = 1 if i['val'] else (2 if i['test'] else 0)
        train_mask, val_mask, test_mask = mask == 0, mask == 1, mask == 2

        row, col = [], []
        for i in graph_data['links']:
            row.append(i['source'])
            col.append(i['target'])
        edge_index = torch.stack([torch.tensor(row), torch.tensor(col)], dim=0)
        edge_index, _ = remove_self_loops(edge_index)
        edge_index = to_undirected(edge_index, x.size(0))  # Internal coalesce.

        with open(self.raw_paths[2], 'r') as f:
            y_data = json.load(f)

        y = []
        for i in range(len(y_data)):
            y.append(y_data[str(i)])
        y = torch.tensor(y, dtype=torch.float)

        data = Data(x=x, edge_index=edge_index, y=y)
        data.train_mask = train_mask
        data.val_mask = val_mask
        data.test_mask = test_mask

        data = data if self.pre_transform is None else self.pre_transform(data)
        data, slices = self.collate([data])
        torch.save((data, slices), self.processed_paths[0])

    def __repr__(self):
        return '{}()'.format(self.__class__.__name__)
