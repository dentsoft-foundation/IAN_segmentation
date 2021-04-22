# Copyright 2020 MONAI Consortium
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import os
import shutil
import sys
import tempfile
from glob import glob

import nibabel as nib
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader

import monai
from monai.data import NiftiSaver, create_test_image_3d, list_data_collate
from monai.engines import get_devices_spec
from monai.inferers import sliding_window_inference
from monai.metrics import DiceMetric
from monai.networks.nets import UNet
from monai.transforms import AddChanneld, AsChannelFirstd, Compose, LoadNiftid, ScaleIntensityd, ToTensord


def main():
    monai.config.print_config()
    logging.basicConfig(stream=sys.stdout, level=logging.INFO)
    
    tempdir = "/media/mayotic/pool/MONAI_TEST/TEST_EVAL/"
    
    images = sorted(glob(os.path.join(tempdir+"imagesTest", "*.nii.gz")))
    segs = sorted(glob(os.path.join(tempdir+"labelsTest", "*.nii.gz")))
    val_files = [{"img": img, "seg": seg} for img, seg in zip(images, segs)]

    # define transforms for image and segmentation
    val_transforms = Compose(
        [
            LoadNiftid(keys=["img", "seg"]),
            #AsChannelFirstd(keys=["img", "seg"], channel_dim=-1),
            AddChanneld(keys=["img", "seg"]),
            ScaleIntensityd(keys="img"),
            ToTensord(keys=["img", "seg"]),
        ]
    )
    val_ds = monai.data.Dataset(data=val_files, transform=val_transforms)
    # sliding window inference need to input 1 image in every iteration
    val_loader = DataLoader(val_ds, batch_size=1, num_workers=4, collate_fn=list_data_collate)
    dice_metric = DiceMetric(include_background=True, to_onehot_y=False, sigmoid=True, reduction="mean")

    # try to use all the available GPUs
    #devices = get_devices_spec(None)
    devices = torch.device("cuda:0")
    model = UNet(
        dimensions=3,
        in_channels=1,
        out_channels=1,
        channels=(16, 32, 64, 128, 256),
        strides=(2, 2, 2, 2),
        num_res_units=2,
    )#.to(devices[0])
    
    model = nn.DataParallel(model)
    model.to(devices)

    model.load_state_dict(torch.load("best_metric_model_650.pth"))

    # if we have multiple GPUs, set data parallel to execute sliding window inference
    #if len(devices) > 1:
    #    model = torch.nn.DataParallel(model, device_ids=devices)

    model.eval()
    with torch.no_grad():
        metric_sum = 0.0
        metric_count = 0
        saver = NiftiSaver(output_dir="./output")
        for val_data in val_loader:
            val_images, val_labels = val_data["img"].to(devices), val_data["seg"].to(devices)
            # define sliding window size and batch size for windows inference
            roi_size = (96, 96, 96)
            sw_batch_size = 4
            val_outputs = sliding_window_inference(val_images, roi_size, sw_batch_size, model)
            value = dice_metric(y_pred=val_outputs, y=val_labels)
            metric_count += len(value)
            metric_sum += value.item() * len(value)
            val_outputs = (val_outputs.sigmoid() >= 0.5).float()
            saver.save_batch(val_outputs, val_data["img_meta_dict"])
        metric = metric_sum / metric_count
        print("evaluation metric:", metric)


if __name__ == "__main__":
    main()
