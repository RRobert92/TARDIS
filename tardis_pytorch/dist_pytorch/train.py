#######################################################################
#  TARDIS - Transformer And Rapid Dimensionless Instance Segmentation #
#                                                                     #
#  New York Structural Biology Center                                 #
#  Simons Machine Learning Center                                     #
#                                                                     #
#  Robert Kiewisz, Tristan Bepler                                     #
#  MIT License 2021 - 2023                                            #
#######################################################################

import sys
from os import getcwd
from typing import Optional

import torch
from torch import optim

from tardis_pytorch.dist_pytorch.dist import CDIST, DIST
from tardis_pytorch.dist_pytorch.trainer import CDistTrainer, DistTrainer
from tardis_pytorch.dist_pytorch.utils.utils import check_model_dict
from tardis_pytorch.utils.device import get_device
from tardis_pytorch.utils.logo import TardisLogo
from tardis_pytorch.utils.losses import (
    AdaptiveDiceLoss,
    BCEDiceLoss,
    BCELoss,
    CELoss,
    ClBCE,
    ClDice,
    DiceLoss,
    SigmoidFocalLoss,
)
from tardis_pytorch.utils.trainer import ISR_LR

# Setting for stable release to turn off all debug APIs
torch.backends.cudnn.benchmark = True
torch.autograd.set_detect_anomaly(mode=False)
torch.autograd.profiler.profile(enabled=False)
torch.autograd.profiler.emit_nvtx(enabled=False)


def train_dist(
    dataset_type: str,
    train_dataloader,
    test_dataloader,
    model_structure: dict,
    checkpoint: Optional[str] = None,
    loss_function="bce",
    learning_rate=0.001,
    lr_scheduler=False,
    early_stop_rate=10,
    device="gpu",
    epochs=1000,
):
    """
    Wrapper for DIST or C_DIST models.

    Args:
        dataset_type (str):
        train_dataloader (torch.DataLoader): DataLoader with train dataset.
        test_dataloader (torch.DataLoader): DataLoader with test dataset.
        model_structure (dict): Dictionary with model setting.
        checkpoint (None, optional): Optional, DIST model checkpoint.
        loss_function (str): Type of loss function.
        learning_rate (float): Learning rate.
        lr_scheduler (bool): If True, LR_scheduler is used with training.
        early_stop_rate (int): Define max. number of epoch's without improvements
        after which training is stopped.
        device (torch.device): Device on which model is trained.
        epochs (int): Max number of epoch's.
    """
    """Losses"""
    losses_f = {
        "AdaptiveDiceLoss": AdaptiveDiceLoss(diagonal=True),
        "BCELoss": BCELoss(diagonal=True),
        "BCEDiceLoss": BCEDiceLoss(diagonal=True),
        "CELoss": CELoss(diagonal=True),
        "DiceLoss": DiceLoss(diagonal=True),
        "ClDice": ClDice(diagonal=True),
        "ClBCE": ClBCE(diagonal=True),
        "SigmoidFocalLoss": SigmoidFocalLoss(diagonal=True),
    }

    """Check input variable"""
    model_structure = check_model_dict(model_structure)

    if not isinstance(device, torch.device) and isinstance(device, str):
        device = get_device(device)

    """Build DIST model"""
    if model_structure["dist_type"] == "instance":
        model = DIST(
            n_out=model_structure["n_out"],
            node_input=model_structure["node_input"],
            node_dim=model_structure["node_dim"],
            edge_dim=model_structure["edge_dim"],
            num_layers=model_structure["num_layers"],
            num_heads=model_structure["num_heads"],
            rgb_embed_sigma=model_structure["rgb_embed_sigma"],
            coord_embed_sigma=model_structure["coord_embed_sigma"],
            dropout_rate=model_structure["dropout_rate"],
            structure=model_structure["structure"],
            predict=False,
        )
    elif model_structure["dist_type"] == "semantic":
        model = CDIST(
            n_out=model_structure["n_out"],
            node_input=model_structure["node_input"],
            node_dim=model_structure["node_dim"],
            edge_dim=model_structure["edge_dim"],
            num_layers=model_structure["num_layers"],
            num_heads=model_structure["num_heads"],
            num_cls=model_structure["num_cls"],
            rgb_embed_sigma=model_structure["rgb_embed_sigma"],
            coord_embed_sigma=model_structure["coord_embed_sigma"],
            dropout_rate=model_structure["dropout_rate"],
            structure=model_structure["structure"],
            predict=False,
        )
    else:
        tardis_logo = TardisLogo()
        tardis_logo(text_1=f"ValueError: Model type: {type} is not supported!")
        sys.exit()

    """Build TARDIS progress bar output"""
    if model_structure["node_dim"] == 0:
        node_sigma = ""
    elif model_structure["rgb_embed_sigma"] == 0:
        node_sigma = ", [Linear] node_sigma"
    else:
        node_sigma = f", {model_structure['rgb_embed_sigma']} node_sigma"

    print_setting = [
        f"Training is started on {device} for DIST-{model_structure['structure']}",
        f"Local dir: {getcwd()}",
        f"Training for {model_structure['dist_type']} with "
        f"No. of Layers: {model_structure['num_layers']} and "
        f"{model_structure['num_heads']} heads",
        f"Layers are build of {model_structure['node_dim']} nodes, "
        f"{model_structure['edge_dim']} edges, "
        f"{model_structure['coord_embed_sigma']} edge_sigma{node_sigma}",
    ]

    """Optionally: Load checkpoint for retraining"""
    if checkpoint is not None:
        save_train = torch.load(checkpoint, map_location=device)

        if "model_struct_dict" in save_train.keys():
            model_dict = save_train["model_struct_dict"]
            globals().update(model_dict)

        model.load_state_dict(save_train["model_state_dict"])

    model = model.to(device)

    """Define loss function for training"""
    loss_fn = losses_f["BCELoss"]
    if loss_function in losses_f:
        loss_fn = losses_f[loss_function]

    """Build training optimizer"""
    if lr_scheduler:
        optimizer = optim.Adam(params=model.parameters(), betas=(0.9, 0.98), eps=1e-9)
    else:
        optimizer = optim.Adam(
            params=model.parameters(), lr=learning_rate, betas=(0.9, 0.98), eps=1e-9
        )

    """Optionally: Build learning rate scheduler"""
    if lr_scheduler:
        optimizer = ISR_LR(optimizer, lr_mul=learning_rate)

    """Optionally: Checkpoint model"""
    if checkpoint is not None:
        optimizer.load_state_dict(save_train["optimizer_state_dict"])
        del save_train

    """Build trainer"""
    if dataset_type in ["filament", "MT", "Mem"]:
        dataset_type = 2
    else:
        dataset_type = 4

    if model_structure["dist_type"] == "instance":
        train = DistTrainer(
            model=model,
            structure=model_structure,
            instance_cov=dataset_type,
            device=device,
            criterion=loss_fn,
            optimizer=optimizer,
            print_setting=print_setting,
            training_DataLoader=train_dataloader,
            validation_DataLoader=test_dataloader,
            epochs=epochs,
            lr_scheduler=lr_scheduler,
            early_stop_rate=early_stop_rate,
            checkpoint_name=model_structure["dist_type"],
        )
    elif model_structure["dist_type"] == "semantic":
        train = CDistTrainer(
            model=model,
            structure=model_structure,
            device=device,
            criterion=loss_fn,
            optimizer=optimizer,
            print_setting=print_setting,
            training_DataLoader=train_dataloader,
            validation_DataLoader=test_dataloader,
            epochs=epochs,
            lr_scheduler=lr_scheduler,
            early_stop_rate=early_stop_rate,
            checkpoint_name=model_structure["dist_type"],
        )

    """Train"""
    train.run_trainer()