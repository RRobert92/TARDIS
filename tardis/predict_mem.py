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
import time
import warnings
from os import getcwd, listdir
from os.path import join
from typing import Optional

import click
import numpy as np
import tifffile.tifffile as tif

from tardis.spindletorch.data_processing.stitch import StitchImages
from tardis.spindletorch.data_processing.trim import scale_image, trim_with_stride
from tardis.spindletorch.datasets.dataloader import PredictionDataset
from tardis.utils.device import get_device
from tardis.utils.export_data import to_mrc
from tardis.utils.load_data import import_am, load_image
from tardis.utils.logo import print_progress_bar, TardisLogo
from tardis.utils.nomalization import MinMaxNormalize, RescaleNormalize
from tardis.utils.predictor import Predictor
from tardis.utils.setup_envir import build_temp_dir, clean_up
from tardis.version import version

warnings.simplefilter("ignore", UserWarning)


@click.command()
@click.option('-dir', '--dir',
              default=getcwd(),
              type=str,
              help='Directory with images for prediction with CNN model.',
              show_default=True)
@click.option('-ps', '--patch_size',
              default=128,
              type=int,
              help='Size of image size used for prediction.',
              show_default=True)
@click.option('-cnn', '--cnn_network',
              default='fnet_32',
              type=str,
              help='CNN network name.',
              show_default=True)
@click.option('-cch', '--cnn_checkpoint',
              default=None,
              type=str,
              help='If not None, str checkpoints for CNN',
              show_default=True)
@click.option('-ct', '--cnn_threshold',
              default=0.1,
              type=float,
              help='Threshold use for model prediction.',
              show_default=True)
@click.option('-d', '--device',
              default='0',
              type=str,
              help='Define which device use for training: '
                   'gpu: Use ID 0 GPUs '
                   'cpu: Usa CPU '
                   '0-9 - specified GPU device id to use',
              show_default=True)
@click.version_option(version=version)
def main(dir: str,
         patch_size: int,
         cnn_network: str,
         cnn_threshold: float,
         device: str,
         cnn_checkpoint: Optional[str] = None):
    """
    MAIN MODULE FOR PREDICTION MT WITH TARDIS-PYTORCH
    """
    """Initial Setup"""
    tardis_progress = TardisLogo()
    tardis_progress(title='Fully-automatic Membrane segmentation module')

    # Searching for available images for prediction
    available_format = ('.tif', '.mrc', '.rec', '.am', '.map')
    output = join(dir, 'temp', 'Predictions')
    am_output = join(dir, 'Predictions')

    predict_list = [f for f in listdir(dir) if f.endswith(available_format)]

    # Tardis progress bar update
    if len(predict_list) == 0:
        tardis_progress(title='Fully-automatic Membrane segmentation module',
                        text_1=f'Found {len(predict_list)} images to predict!',
                        text_5='Point Cloud: Nan',
                        text_7='Current Task: NaN',
                        text_8='Tardis Error: Wrong directory:',
                        text_9=f'Given {dir} is does not contain any recognizable file formats!')
        sys.exit()
    else:
        tardis_progress(title='Fully-automatic Membrane segmentation module',
                        text_1=f'Found {len(predict_list)} images to predict!',
                        text_5='Point Cloud: In processing...',
                        text_7='Current Task: Set-up environment...')

    # Hard fix for dealing with tif file lack of pixel sizes...
    tif_px = None
    if np.any([True for x in predict_list if x.endswith(('.tif', '.tiff'))]):
        tif_px = click.prompt('Detected .tif files, please provide pixel size:',
                              type=float)

    # Build handler's
    normalize = RescaleNormalize(clip_range=(1, 99))  # Normalize histogram
    minmax = MinMaxNormalize()

    image_stitcher = StitchImages()

    device = get_device(device)
    cnn_network = cnn_network.split('_')
    if not len(cnn_network) == 2:
        tardis_progress(title='Fully-automatic Membrane segmentation module',
                        text_1=f'Found {len(predict_list)} images to predict!',
                        text_5='Point Cloud: Nan',
                        text_7='Current Task: NaN',
                        text_8='Tardis Error: Given CNN type is wrong!:',
                        text_9=f'Given {cnn_network} but should be e.g. `unet_32`')
        sys.exit()

    # Build CNN network with loaded pre-trained weights
    predict_cnn = Predictor(checkpoint=cnn_checkpoint,
                            network=cnn_network[0],
                            subtype=cnn_network[1],
                            model_type='cryo_mem',
                            img_size=patch_size,
                            device=device)

    """Process each image with CNN and DIST"""
    tardis_progress = TardisLogo()
    for id, i in enumerate(sorted(predict_list)):
        """Pre-Processing"""
        if i.endswith('CorrelationLines.am'):
            continue

        out_format = 0
        if i.endswith(('.tif', '.mrc', '.rec', '.map')):
            out_format = 4
        elif i.endswith('.tiff'):
            out_format = 5
        elif i.endswith('.am'):
            out_format = 3

        # Tardis progress bar update
        tardis_progress(title='Fully-automatic Membrane segmentation module ',
                        text_1=f'Found {len(predict_list)} images to predict!',
                        text_3=f'Image {id + 1}/{len(predict_list)}: {i}',
                        text_4='Pixel size: Nan A',
                        text_5='Point Cloud: In processing...',
                        text_7='Current Task: Preprocessing for CNN...')

        # Build temp dir
        build_temp_dir(dir=dir)

        # Cut image for smaller image
        if i.endswith('.am'):
            image, px, _, _ = import_am(am_file=join(dir, i))
        else:
            image, px = load_image(join(dir, i))

        if tif_px is not None:
            px = tif_px

        # Check image structure and normalize histogram
        image = normalize(image)  # Rescale image intensity
        if not image.min() >= 0 or not image.max() <= 1:  # Normalized between 0 and 1
            image = minmax(image)
        elif image.min() >= -1 and image.max() <= 1:
            image = minmax(image)

        if not image.dtype == np.float32:
            tardis_progress(title='Fully-automatic Membrane segmentation module',
                            text_1=f'Found {len(predict_list)} images to predict!',
                            text_3=f'Image {id + 1}/{len(predict_list)}: {i}',
                            text_5='Point Cloud: Nan A',
                            text_7='Current Task: NaN',
                            text_8=f'Tardis Error: Error while loading image {i}:',
                            text_9=f'Image loaded correctly, but output format {image.dtype} is not float32!')
            sys.exit()

        # Calculate parameters for normalizing image pixel size
        scale_factor = px / 16.56
        org_shape = image.shape
        scale_shape = tuple(np.multiply(org_shape, scale_factor).astype(np.int16))

        # Tardis progress bar update
        tardis_progress(title='Fully-automatic Membrane segmentation module ',
                        text_1=f'Found {len(predict_list)} images to predict!',
                        text_3=f'Image {id + 1}/{len(predict_list)}: {i}',
                        text_4=f'Pixel size: {px} A',
                        text_5='Point Cloud: In processing...',
                        text_7=f'Current Task: Sub-dividing images for {patch_size} size')

        # Cut image for fix patch size and normalizing image pixel size
        trim_with_stride(image=image.astype(np.float32),
                         scale=scale_shape,
                         trim_size_xy=patch_size,
                         trim_size_z=patch_size,
                         output=join(dir, 'temp', 'Patches'),
                         image_counter=0,
                         clean_empty=False,
                         stride=10)
        del image

        # Setup CNN dataloader
        patches_dl = PredictionDataset(img_dir=join(dir, 'temp', 'Patches'))

        """CNN prediction"""
        iter_time = 1
        for j in range(len(patches_dl)):
            if j % iter_time == 0:
                # Tardis progress bar update
                tardis_progress(title='Fully-automatic Membrane segmentation module ',
                                text_1=f'Found {len(predict_list)} images to predict!',
                                text_3=f'Image {id + 1}/{len(predict_list)}: {i}',
                                text_4=f'Pixel size: {px} A',
                                text_5='Point Cloud: In processing...',
                                text_7='Current Task: CNN prediction...',
                                text_8=print_progress_bar(j, len(patches_dl)))

            # Pick image['s]
            input, name = patches_dl.__getitem__(j)

            if j == 0:
                start = time.time()

                # Predict & Threshold
                input = predict_cnn.predict(input[None, :])
                if cnn_threshold != 0:
                    input = np.where(input >= cnn_threshold, 1, 0).astype(np.uint8)

                end = time.time()
                iter_time = 10 // (end - start)  # Scale progress bar refresh to 10s
                if iter_time <= 1:
                    iter_time = 1
            else:
                # Predict & Threshold
                input = predict_cnn.predict(input[None, :])
                if cnn_threshold != 0:
                    input = np.where(input >= cnn_threshold, 1, 0).astype(np.uint8)

            tif.imwrite(join(output, f'{name}.tif'),
                        np.array(input, dtype=input.dtype))

        """Post-Processing"""
        # Tardis progress bar update
        tardis_progress(title='Fully-automatic Membrane segmentation module ',
                        text_1=f'Found {len(predict_list)} images to predict!',
                        text_3=f'Image {id + 1}/{len(predict_list)}: {i}',
                        text_4=f'Original pixel size: {px} A',
                        text_5='Point Cloud: In processing...',
                        text_7='Current Task: Stitching...')

        # Stitch predicted image patches
        image = image_stitcher(image_dir=output, mask=True,
                               dtype=input.dtype)[:org_shape[0],
                                                  :org_shape[1],
                                                  :org_shape[2]]

        # Restored original image pixel size
        image, _ = scale_image(image=image, scale=org_shape)

        # Check if predicted image
        if not image.shape == org_shape:
            tardis_progress(title='Fully-automatic Membrane segmentation module',
                            text_1=f'Found {len(predict_list)} images to predict!',
                            text_3=f'Image {id + 1}/{len(predict_list)}: {i}',
                            text_4=f'Original pixel size: {px} A',
                            text_5='Point Cloud: NaN.',
                            text_7='Last Task: Stitching/Scaling/Make correction...',
                            text_8=f'Tardis Error: Error while converting to {px} A pixel size.',
                            text_9=f'Org. shape {org_shape} is not the same as converted shape {image.shape}')
            sys.exit()

        if join(dir, i).endswith('.mrc'):
            to_mrc(data=image,
                   file_dir=join(am_output, f'{i[:-out_format]}_CNN.mrc'))

        tif.imwrite(join(am_output, f'{i[:-out_format]}_CNN.tif'),
                    image)

        """Clean-up temp dir"""
        clean_up(dir=dir)


if __name__ == '__main__':
    main()