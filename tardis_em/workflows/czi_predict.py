#######################################################################
#  TARDIS - Transformer And Rapid Dimensionless Instance Segmentation #
#                                                                     #
#  New York Structural Biology Center                                 #
#  Simons Machine Learning Center                                     #
#                                                                     #
#  Robert Kiewisz, Tristan Bepler                                     #
#  MIT License 2021 - 2024                                            #
#######################################################################

import asyncio
import boto3
import pandas as pd
import torch
import tifffile.tifffile as tif
import numpy as np
import json
import time

from os import mkdir, remove
from os.path import join, isdir, isfile
from botocore import UNSIGNED
from botocore.config import Config
from cryoet_data_portal import Client, Tomogram
from s3transfer import S3Transfer
from shutil import rmtree, move

from tardis_em.cnn.data_processing.stitch import StitchImages
from tardis_em.cnn.data_processing.trim import trim_with_stride
from tardis_em.cnn.datasets.dataloader import PredictionDataset
from tardis_em.cnn.utils.utils import scale_image
from tardis_em.dist_pytorch.datasets.patches import PatchDataSet
from tardis_em.dist_pytorch.utils.build_point_cloud import BuildPointCloud
from tardis_em.dist_pytorch.utils.segment_point_cloud import PropGreedyGraphCut
from tardis_em.utils.load_data import load_image, mrc_read_header
from tardis_em.utils.logo import TardisLogo, print_progress_bar
from tardis_em.utils.device import get_device
from tardis_em.utils.predictor import Predictor
from tardis_em.utils.normalization import RescaleNormalize, MeanStdNormalize
from tardis_em.utils.export_data import to_mrc
from tardis_em.utils.spline_metric import sort_by_length

name = "czi_predict"
help = "Predict entire CZI-dataportal"


async def get_from_aws(url):
    s3 = boto3.client("s3", config=Config(signature_version=UNSIGNED))
    s3.download_file("cryoet-data-portal-public", url, url.split("/")[-1])

    print(f"Downloaded: {url.split('/')[-1]}")


async def upload_to_aws(
    aws_key, aws_secret, bucket, id_, data_name, file_name, remove_=True
):
    if data_name is None:
        client = boto3.client(
            "s3", aws_access_key_id=aws_key, aws_secret_access_key=aws_secret
        )
        transfer = S3Transfer(client)
        transfer.upload_file(
            file_name,
            bucket,
            join("robert_kiewisz_tardis_01_2024", id_, file_name),
        )
    try:
        client = boto3.client(
            "s3", aws_access_key_id=aws_key, aws_secret_access_key=aws_secret
        )
        transfer = S3Transfer(client)
        transfer.upload_file(
            file_name,
            bucket,
            join("robert_kiewisz_tardis_01_2024", id_, data_name, file_name),
        )
        if remove_:
            remove(file_name)
        else:
            move(file_name, join("TARDIS", file_name))

        print(f"Uploaded: {file_name}")
    except:
        print(f"Upload fail: {file_name}")


class ProcessTardisForCZI:
    def __init__(self, allocate_gpu: str, predict="Membrane"):
        self.device = get_device(allocate_gpu)
        self.predict = predict
        self.normalize_px = 15 if self.predict == "Membrane" else 25
        self.image_stitcher = StitchImages()
        self.post_processes = BuildPointCloud()

        self.output = join("temp", "Predictions")
        self.patches = join("temp", "Patches")
        self.results = "TARDIS"

        # Normalize histogram
        self.normalize = RescaleNormalize(clip_range=(1, 99))
        self.mean_std = MeanStdNormalize()

        self.terminal = TardisLogo()
        self.terminal(title="Predict CIZ-Cryo-EM data-port datasets")

        # Load CNN model
        self.model_cnn = Predictor(
            checkpoint=None,
            network="fnet_attn",
            subtype="32",
            model_type=(
                "membrane_3d" if self.predict == "Membrane" else "microtubules_3d"
            ),
            img_size=128,
            sigmoid=False,
            device=self.device,
        )

        self.model_dist = Predictor(
            checkpoint=None,
            network="dist",
            subtype="triang",
            model_type="3d" if self.predict == "Membrane" else "2d",
            device=self.device,
        )

        self.patch_pc = PatchDataSet(max_number_of_points=1000, graph=False)
        self.segment_pc = PropGreedyGraphCut(
            threshold=0.5, connection=4 if self.predict == "Membrane" else 2
        )

    @staticmethod
    def clean_temp():
        if isdir("temp"):
            rmtree("temp")

    def build_temp(self):
        """
        Standard set-up for creating new temp dir for cnn prediction.

        Args:
            dir_ (str): Directory where folder will be build.
        """
        if not isdir(self.results):
            mkdir(self.results)

        self.clean_temp()

        mkdir("temp")
        mkdir(self.patches)
        mkdir(self.output)

    def predict_cnn(self, dataloader):
        for j in range(len(dataloader)):
            input_, name = dataloader.__getitem__(j)
            input_ = self.model_cnn.predict(input_[None, :], rotate=False)
            tif.imwrite(join(self.output, f"{name}.tif"), input_)

    def predict_dist(self, coords_df):
        x = []
        with torch.no_grad():
            for i in coords_df:
                x.append(self.model_dist.predict(x=i[None, :], y=None))

        return x

    @staticmethod
    def prediction_stat(name: list, pc: np.ndarray):
        if isfile("stat.json"):
            with open("stat.json", "r") as file:
                data = json.load(file)
        else:
            data = {}

        try:
            name_ = name[0]
            data[name_] = {}
            data[name_]['Pixel_Size'] = name[1]
            data[name_]['Segmentation_time'] = name[2]
            data[name_]["Number_of_Points"] = len(pc)
            data[name_]["Number_of_Instances"] = len(np.unique(pc[:, 0]))
            data[name_]["Size_of_Each_Instance"] = [
                len(pc[pc[:, 0] == x, :]) for x in np.unique(pc[:, 0])
            ]

            size_ = np.array(data[name_]["Size_of_Each_Instance"])
            data[name_]["Contain_Membrane"] = 1 if np.any(size_ > 1000) else 0
            if data[name_]["Contain_Membrane"] == 0:
                data[name_]["Maybe_Contain_Membrane"] = np.any(size_ > 250)
            else:
                data[name_]["Maybe_Contain_Membrane"] = 1

            with open("stat.json", "w") as file:
                json.dump(data, file, indent=4)
        except TypeError:
            name_ = name[0]
            data[name_] = {}
            data[name_]['Pixel_Size'] = name[1]
            data[name_]['Segmentation_time'] = name[2]
            data[name_]["Number_of_Points"] = 'NaN'
            data[name_]["Number_of_Instances"] = 'NaN'
            data[name_]["Size_of_Each_Instance"] = 'NaN'

            data[name_]["Contain_Membrane"] = 'NaN'
            data[name_]["Maybe_Contain_Membrane"] = 'NaN'

            with open("stat.json", "w") as file:
                json.dump(data, file, indent=4)

    async def __call__(
        self, data: np.ndarray, px: float, header, name: str, progress: tuple
    ):
        """Build temp dir"""
        self.build_temp()

        """Load and prepare dataset for prediction"""
        self.terminal(
            text_1="TARDIS segmentation:",
            text_2="Preprocess data...",
            text_3=f"Dataset: [{name}]",
            text_4=f"Pixel_size: {px}",
            text_6=print_progress_bar(progress[0], progress[1]),
        )

        data = self.normalize(self.mean_std(data)).astype(np.float32)
        tif.imwrite(join(self.results, name + ".tif"), data.sum(0).astype(np.float32))

        if not data.min() >= -1 or not data.max() <= 1:  # Image not between in -1 and 1
            if data.min() >= 0 and data.max() <= 1:
                data = (data - 0.5) * 2  # shift to -1 - 1
            elif data.min() >= 0 and data.max() <= 255:
                data = data / 255  # move to 0 - 1
                data = (data - 0.5) * 2  # shift to -1 - 1

        scale_factor = px / self.normalize_px
        org_shape = data.shape
        scale_shape = np.multiply(org_shape, scale_factor).astype(np.int16)
        scale_shape = [int(i) for i in scale_shape]

        start = time.time()
        trim_with_stride(
            image=data,
            scale=scale_shape,
            trim_size_xy=128,
            trim_size_z=128,
            output=self.patches,
            image_counter=0,
            clean_empty=False,
            stride=round(128 * 0.125),
        )

        """TARDIS - Semantic Segmentation"""
        self.terminal(
            text_1="TARDIS segmentation:",
            text_2="Semantic Segmentation...",
            text_3=f"Dataset: [{name}]",
            text_4=f"Pixel_size: {px}",
            text_6=print_progress_bar(progress[0], progress[1]),
        )

        self.name = name
        self.px = px
        self.progress = (progress[0], progress[1])
        self.predict_cnn(
            dataloader=PredictionDataset(join(self.patches, "imgs")),
        )
        data = self.image_stitcher(image_dir=self.output, mask=False, dtype=np.float32)[
            : scale_shape[0], : scale_shape[1], : scale_shape[2]
        ]
        data, _ = scale_image(image=data, scale=org_shape, nn=False)
        data = torch.sigmoid(torch.from_numpy(data)).cpu().detach().numpy()

        data = np.where(data > 0.5, 1, 0).astype(np.uint8)
        to_mrc(data, px, name + "_semantic.mrc", header)  # To upload back to AWS
        tif.imwrite(
            join(self.results, name + "_semantic.tif"), data.sum(0).astype(np.uint8)
        )  # Store for NYSBC
        self.clean_temp()

        """TARDIS - Instance Segmentation"""
        self.terminal(
            text_1="TARDIS segmentation:",
            text_2="Instance Segmentation...",
            text_3=f"Dataset: [{name}]",
            text_4=f"Pixel_size: {px}",
            text_6=print_progress_bar(progress[0], progress[1]),
        )

        _, pc_ld = BuildPointCloud().build_point_cloud(
            image=data,
            EDT=False,
            down_sampling=5,
            as_2d=True if self.predict == "Membrane" else False,
        )
        coords_df, _, output_idx, _ = self.patch_pc.patched_dataset(coord=pc_ld)
        x = self.predict_dist(coords_df)
        pc = self.segment_pc.patch_to_segment(
            graph=x,
            coord=pc_ld,
            idx=output_idx,
            sort=False if self.predict == "Membrane" else True,
            prune=25 if self.predict == "Membrane" else 10,
        )
        end_time = np.round(time.time() - start, 0) / 60  # Minutes

        self.prediction_stat([name, px, end_time], pc)
        pc = sort_by_length(pc)

        pc = pd.DataFrame(pc)
        pc.to_csv(
            name + "_instance.csv",
            header=["IDs", "X [A]", "Y [A]", "Z [A]"],
            index=False,
            sep=",",
        )


async def main():
    aws_dir = "aws_czi.csv"
    allocate_gpu = "7"
    predict = "Membrane"

    assert predict in ["Membrane", "Microtubule"]

    terminal = TardisLogo()
    terminal(title="Predict CIZ-Cryo-EM data-port datasets")
    process_tardis = ProcessTardisForCZI(allocate_gpu, predict)

    # AWS pre-setting
    aws = np.genfromtxt(aws_dir, delimiter=",", dtype=str)
    aws_key = aws[0]
    aws_secret = aws[1]
    client = Client()
    bucket = "cryoetportal-rawdatasets-dev"

    # Get all tomograms with voxel spacing <= 10
    all_tomogram = list(
        Tomogram.find(
            client,
        )
    )

    # S3 URIs for MRCs
    urls = [t.s3_mrc_scale0 for t in all_tomogram]
    folder_names = [s[31:36] for s in urls]
    file_names = [s.split("/")[4] for s in urls]
    czi_px = [float(s.split("/")[6][12:]) for s in urls]

    terminal(
        text_1="Prediction:",
        text_3="Dataset: []",
        text_5=print_progress_bar(0, len(urls)),
    )

    tasks = []
    next_download = asyncio.create_task(get_from_aws(urls[0][31:]))
    for idx, (folder_name, file_name, px_czi) in enumerate(
        zip(folder_names, file_names, czi_px)
    ):
        terminal(
            text_1="Waiting for Data from AWS:",
            text_3=f"Dataset: [{file_name}]",
            text_6=print_progress_bar(idx, len(urls)),
        )
        await next_download

        data, px = load_image(file_name + ".mrc", False)
        header = mrc_read_header(file_name + ".mrc")
        remove(file_name + ".mrc")

        if px != px_czi:
            px = px_czi

        terminal(
            text_1="TARDIS segmentation:",
            text_3=f"Dataset: [{file_name}]",
            text_4=f"Pixel_size: {px}",
            text_6=print_progress_bar(idx, len(urls)),
        )

        tardis = asyncio.create_task(
            process_tardis(data, px, header, file_name, (idx, len(urls)))
        )

        # If there's a next file, start downloading it now
        if idx + 1 < len(urls):
            next_download = asyncio.create_task(get_from_aws(urls[idx + 1][31:]))

        await tardis

        # Upload the processed files
        tasks += [
            asyncio.create_task(
                upload_to_aws(
                    aws_key,
                    aws_secret,
                    bucket,
                    folder_name,
                    file_name,
                    file_name + "_semantic.mrc",
                )
            ),
            asyncio.create_task(
                upload_to_aws(
                    aws_key,
                    aws_secret,
                    bucket,
                    folder_name,
                    file_name,
                    file_name + "_instance.csv",
                    remove_=False,
                )
            ),
            upload_to_aws(
                    aws_key,
                    aws_secret,
                    bucket,
                    'TARDIS_Prediction_Stats',
                    None,
                    "stat.json",
                    remove_=False,
                ),
        ]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())