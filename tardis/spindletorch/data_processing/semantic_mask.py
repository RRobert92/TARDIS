#######################################################################
#  TARDIS - Transformer And Rapid Dimensionless Instance Segmentation #
#                                                                     #
#  New York Structural Biology Center                                 #
#  Simons Machine Learning Center                                     #
#                                                                     #
#  Robert Kiewisz, Tristan Bepler                                     #
#  MIT License 2021 - 2023                                            #
#######################################################################

import numpy as np

from tardis.spindletorch.data_processing.draw_mask import draw_mask
from tardis.spindletorch.data_processing.interpolation import interpolation
from tardis.utils.errors import TardisError


def draw_semantic(mask_size: tuple,
                  coordinate: np.ndarray,
                  pixel_size: float,
                  circle_size=250) -> np.ndarray:
    """
    Module to build semantic mask from corresponding coordinates

    Args:
        mask_size (tuple): Size of array that will hold created mask.
        coordinate (np.ndarray): Segmented coordinates of a shape [Label x X x Y x (Z)].
        pixel_size (float): Pixel size in Angstrom.
        circle_size (int): Size of a circle the label mask in Angstrom.

    Returns:
        np.ndarray: Binary mask with drawn all coordinates as lines.
    """
    assert coordinate.ndim == 2 and coordinate.shape[1] in [3, 4], \
        TardisError('113',
                    'tardis/spindletorch/data_processing/semantic_mask.py',
                    'Coordinates are of not correct shape, expected: '
                    f'shape [Label x X x Y x (Z)] but {coordinate.shape} given!')

    label_mask = np.zeros(mask_size, dtype=np.uint8)
    if pixel_size == 0:
        pixel_size = 1

    r = round((circle_size / 2) / pixel_size)

    if coordinate.shape[1] == 3:  # Draw 2D mask
        mask_shape = 'c'
    else:  # Draw 3D mask
        mask_shape = 's'

    # Number of segments in coordinates
    segments = np.unique(coordinate[:, 0])

    """Build semantic mask by drawing circle in 2D for each coordinate point"""
    for i in segments:
        # Pick coordinates for each segment
        points = coordinate[np.where(coordinate[:, 0] == i)[0]][:, 1:]
        label = interpolation(points)
        all_cz, all_cy, all_cx = [], [], []

        """Draw label"""
        for j in range(len(label)):
            c = label[j, :]  # Point center

            if len(c) == 3:
                cz, cy, cx = draw_mask(r=r,
                                       c=c,
                                       label_mask=label_mask,
                                       segment_shape=mask_shape)
                all_cz.append(cz)
                all_cy.append(cy)
                all_cx.append(cx)
            else:
                cy, cx = draw_mask(r=r,
                                   c=c,
                                   label_mask=label_mask,
                                   segment_shape=mask_shape)
                all_cy.append(cy)
                all_cx.append(cx)

        all_cz, all_cy, all_cx = np.concatenate(all_cz), np.concatenate(all_cy), \
            np.concatenate(all_cx)

        label_mask[all_cz, all_cy, all_cx] = 1

    return np.where(label_mask == 1, 1, 0).astype(np.uint8)