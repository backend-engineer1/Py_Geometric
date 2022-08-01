import torch
from tqdm import tqdm

from torch_geometric.nn import GAT, to_hetero


class HeteroGAT(torch.nn.Module):
    def __init__(self, metadata, hidden_channels, num_layers, output_channels,
                 num_heads):
        super().__init__()
        self.model = to_hetero(
            GAT((-1, -1), hidden_channels, num_layers, output_channels,
                add_self_loops=False, heads=num_heads), metadata)

    @torch.no_grad()
    def inference(self, loader, device, progress_bar=False):
        self.model.eval()
        if progress_bar:
            loader = tqdm(loader, desc="Inference")
        for batch in loader:
            batch = batch.to(device)
            self.model(batch.x_dict, batch.edge_index_dict)
