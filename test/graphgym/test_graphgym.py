import os.path as osp
import torch
import random
import sys
import shutil

from collections import namedtuple

from torch_geometric import seed_everything
from torch_geometric.graphgym.train import train
from torch_geometric.graphgym.loader import create_loader
from torch_geometric.graphgym.model_builder import create_model
from torch_geometric.graphgym.models.head import GNNNodeHead
from torch_geometric.graphgym.logger import set_printing, create_logger
from torch_geometric.graphgym.models.gnn import FeatureEncoder, GNNStackStage
from torch_geometric.graphgym.config import (cfg, dump_cfg, set_run_dir,
                                             set_agg_dir, load_cfg)
from torch_geometric.graphgym.utils import (agg_runs, params_count,
                                            auto_select_device)
from torch_geometric.graphgym.optimizer import (create_optimizer,
                                                create_scheduler,
                                                OptimizerConfig,
                                                SchedulerConfig)


def test_run_single_graphgym():
    Args = namedtuple('Args', ['cfg_file', 'opts'])
    root = osp.join(osp.dirname(osp.realpath(__file__)))
    args = Args(osp.join(root, 'example_node.yml'), [])

    load_cfg(cfg, args)
    cfg.out_dir = osp.join('/', 'tmp', str(random.randrange(sys.maxsize)))
    cfg.run_dir = osp.join('/', 'tmp', str(random.randrange(sys.maxsize)))
    cfg.dataset.dir = osp.join('/', 'tmp', str(random.randrange(sys.maxsize)))
    dump_cfg(cfg)
    set_printing()

    seed_everything(cfg.seed)
    auto_select_device()
    set_run_dir(cfg.out_dir, args.cfg_file)

    loaders = create_loader()
    assert len(loaders) == 3

    loggers = create_logger()
    assert len(loggers) == 3

    model = create_model()
    assert isinstance(model, torch.nn.Module)
    assert isinstance(model.encoder, FeatureEncoder)
    assert isinstance(model.mp, GNNStackStage)
    assert isinstance(model.post_mp, GNNNodeHead)

    optimizer_config = OptimizerConfig(optimizer=cfg.optim.optimizer,
                                       base_lr=cfg.optim.base_lr,
                                       weight_decay=cfg.optim.weight_decay,
                                       momentum=cfg.optim.momentum)
    optimizer = create_optimizer(model.parameters(), optimizer_config)
    assert isinstance(optimizer, torch.optim.Adam)

    scheduler_config = SchedulerConfig(scheduler=cfg.optim.scheduler,
                                       steps=cfg.optim.steps,
                                       lr_decay=cfg.optim.lr_decay,
                                       max_epoch=cfg.optim.max_epoch)
    scheduler = create_scheduler(optimizer, scheduler_config)
    assert isinstance(scheduler, torch.optim.lr_scheduler.CosineAnnealingLR)

    cfg.params = params_count(model)
    assert cfg.params == 23336

    train(loggers, loaders, model, optimizer, scheduler)

    agg_runs(set_agg_dir(cfg.out_dir, args.cfg_file), cfg.metric_best)

    shutil.rmtree(cfg.out_dir)
    shutil.rmtree(cfg.dataset.dir)
