#######################################################################
#  TARDIS - Transformer And Rapid Dimensionless Instance Segmentation #
#                                                                     #
#  New York Structural Biology Center                                 #
#  Simons Machine Learning Center                                     #
#                                                                     #
#  Robert Kiewisz, Tristan Bepler                                     #
#  MIT License 2021 - 2023                                            #
#######################################################################

import os
import shutil

import torch.nn as nn
from torch.utils.data import DataLoader

from tardis.dist_pytorch.datasets.dataloader import (FilamentDataset, ScannetColorDataset)
from tardis.dist_pytorch.train import train_dist
from tardis.dist_pytorch.trainer import CDistTrainer, DistTrainer
from tardis.utils.device import get_device


def test_init_dist_train():
    DistTrainer(model=nn.Conv1d(1, 2, 3),
                structure={'node_input': 0},
                device='cpu',
                criterion=None,
                optimizer=None,
                print_setting=[],
                training_DataLoader=None,
                validation_DataLoader=None,
                lr_scheduler=None,
                epochs=100,
                early_stop_rate=10,
                checkpoint_name='test')

    CDistTrainer(model=nn.Conv1d(1, 2, 3),
                 structure={'node_input': 0, 'dist_type': 'semantic'},
                 device='cpu',
                 criterion=None,
                 optimizer=None,
                 print_setting=[],
                 training_DataLoader=None,
                 validation_DataLoader=None,
                 lr_scheduler=None,
                 epochs=100,
                 early_stop_rate=10,
                 checkpoint_name='test')

    CDistTrainer(model=nn.Conv1d(1, 2, 3),
                 structure={'node_input': 0, 'dist_type': 'instance'},
                 device='cpu',
                 criterion=None,
                 optimizer=None,
                 print_setting=[],
                 training_DataLoader=None,
                 validation_DataLoader=None,
                 lr_scheduler=None,
                 epochs=100,
                 early_stop_rate=10,
                 checkpoint_name='test')


def test_dist_trainer():
    """Test DIST trainer"""
    structure = {
        'dist_type': 'instance',
        'n_out': 1,
        'node_input': 0,
        'node_dim': None,
        'edge_dim': 4,
        'num_layers': 1,
        'num_heads': 1,
        'coord_embed_sigma': 0.2,
        'dropout_rate': 0.0,
        'structure': 'triang'
    }

    train_dl = FilamentDataset(coord_dir='./tests/test_data/data_loader/filament_mt/train/masks/',
                               coord_format=('.CorrelationLines.am', '.csv'),
                               patch_if=50,
                               train=True)
    train_dl = train_dl + train_dl + train_dl + train_dl
    train_dl = DataLoader(dataset=train_dl, batch_size=1, shuffle=True, pin_memory=True)

    test_dl = FilamentDataset(coord_dir='./tests/test_data/data_loader/filament_mt/test/masks/',
                              coord_format=('.CorrelationLines.am', '.csv'),
                              patch_if=50,
                              train=False)
    test_dl = DataLoader(dataset=test_dl,
                         shuffle=True,
                         pin_memory=True)

    train_dist(train_dataloader=train_dl,
               test_dataloader=test_dl,
               model_structure=structure,
               device=get_device('cpu'),
               epochs=2)

    assert len(os.listdir('./temp_test')) == 4
    shutil.rmtree('./temp_test')

    assert len(os.listdir('./temp_train')) == 4
    shutil.rmtree('./temp_train')

    assert len(os.listdir('./instance_checkpoint')) == 5
    shutil.rmtree('./instance_checkpoint')


def test_c_dist_trainer():
    """Test C_DIST trainer"""
    structure = {
        'dist_type': 'semantic',
        'n_out': 1,
        'node_input': 3,
        'node_dim': 4,
        'edge_dim': 4,
        'num_cls': 200,
        'num_layers': 1,
        'num_heads': 1,
        'coord_embed_sigma': 0.2,
        'dropout_rate': 0.0,
        'structure': 'triang'
    }

    train_dl = ScannetColorDataset(coord_dir='./tests/test_data/data_loader/scannet/train/masks/',
                                   coord_format='.ply',
                                   patch_if=50,
                                   train=True)
    train_dl = train_dl + train_dl + train_dl + train_dl
    train_dl = DataLoader(dataset=train_dl, batch_size=1, shuffle=True, pin_memory=True)

    test_dl = ScannetColorDataset(coord_dir='./tests/test_data/data_loader/scannet/test/masks/',
                                  coord_format='.ply',
                                  patch_if=50,
                                  train=False)
    test_dl = DataLoader(dataset=test_dl,
                         shuffle=True,
                         pin_memory=True)

    train_dist(train_dataloader=train_dl,
               test_dataloader=test_dl,
               model_structure=structure,
               device=get_device('cpu'),
               epochs=2)

    assert len(os.listdir('./temp_test')) == 5
    shutil.rmtree('./temp_test')

    assert len(os.listdir('./temp_train')) == 5
    shutil.rmtree('./temp_train')

    assert len(os.listdir('./semantic_checkpoint')) == 5
    shutil.rmtree('./semantic_checkpoint')