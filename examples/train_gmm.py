# Copyright 2020 InterDigital Communications, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import os
import math
import random
import shutil
import sys

import numpy as np

import torch
import torch.nn as nn
import torch.optim as optim

from torch.utils.data import DataLoader
from torchvision import transforms

from compressai.datasets import ImageFolder
from compressai.layers import GDN
from compressai.models import MeanScaleHyperprior, Cheng2020Attention
from compressai.models.utils import conv, deconv
from compressai.utils.writer import get_writer

def psnr(mse):
    if torch.is_tensor(mse):
        log10 = torch.log10
    else:
        log10 = np.log10
    return 10 * log10(1 / mse)

class AutoEncoder(MeanScaleHyperprior):
    """Simple autoencoder with a factorized prior """

    def __init__(self, C=256):
        super().__init__(N=256, M=C)

        self.encode = nn.Sequential(
            nn.Conv2d(in_channels=3, out_channels=C, kernel_size=5, stride=2, padding=2),
            GDN(C),
            nn.Conv2d(in_channels=C, out_channels=C, kernel_size=5, stride=2, padding=2),
            GDN(C),
            nn.Conv2d(in_channels=C, out_channels=C, kernel_size=5, stride=2, padding=2),
            GDN(C),
            nn.Conv2d(in_channels=C, out_channels=C, kernel_size=5, stride=2, padding=2),
        )

        self.decode = nn.Sequential(
            nn.ConvTranspose2d(in_channels=C, out_channels=C, kernel_size=5, stride=2, padding=2, output_padding=1),
            GDN(C, inverse=True),
            nn.ConvTranspose2d(in_channels=C, out_channels=C, kernel_size=5, stride=2, padding=2, output_padding=1),
            GDN(C, inverse=True),
            nn.ConvTranspose2d(in_channels=C, out_channels=C, kernel_size=5, stride=2, padding=2, output_padding=1),
            GDN(C, inverse=True),
            nn.ConvTranspose2d(in_channels=C, out_channels=3, kernel_size=5, stride=2, padding=2, output_padding=1),
        )

    def forward(self, x):
        y = self.encode(x)
        y_hat, y_likelihoods = self.entropy_bottleneck(y)
        x_hat = self.decode(y_hat)
        return {
            "x_hat": x_hat,
            "likelihoods": {
                "y": y_likelihoods,
            },
        }


class RateDistortionLoss(nn.Module):
    """Custom rate distortion loss with a Lagrangian parameter."""

    def __init__(self, lmbda=1e-2):
        super().__init__()
        self.mse = nn.MSELoss()
        self.lmbda = lmbda

    def forward(self, output, target):
        N, _, H, W = target.size()
        out = {}
        num_pixels = N * H * W

        out["bpp_loss"] = sum(
            (torch.log(likelihoods).sum() / (-math.log(2) * num_pixels))
            for likelihoods in output["likelihoods"].values()
        )
        out["mse_loss"] = self.mse(output["x_hat"], target)
        out["loss"] = self.lmbda * 255 ** 2 * out["mse_loss"] + out["bpp_loss"]

        return out


class AverageMeter:
    """Compute running average."""

    def __init__(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count


class CustomDataParallel(nn.DataParallel):
    """Custom DataParallel to access the module methods."""

    def __getattr__(self, key):
        try:
            return super().__getattr__(key)
        except AttributeError:
            return getattr(self.module, key)


def configure_optimizers(net, args):
    """Separate parameters for the main optimizer and the auxiliary optimizer.
    Return two optimizers"""

    parameters = set(
        p for n, p in net.named_parameters() if not n.endswith(".quantiles")
    )
    aux_parameters = set(
        p for n, p in net.named_parameters() if n.endswith(".quantiles")
    )

    # Make sure we don't have an intersection of parameters
    params_dict = dict(net.named_parameters())
    inter_params = parameters & aux_parameters
    union_params = parameters | aux_parameters

    assert len(inter_params) == 0
    assert len(union_params) - len(params_dict.keys()) == 0

    optimizer = optim.Adam(
        (p for p in parameters if p.requires_grad),
        lr=args.learning_rate,
    )
    aux_optimizer = optim.Adam(
        (p for p in aux_parameters if p.requires_grad),
        lr=args.aux_learning_rate,
    )
    return optimizer, aux_optimizer


def train_one_epoch(
    model, criterion, train_dataloader, optimizer, aux_optimizer, epoch, clip_max_norm, writer
):
    model.train()
    device = next(model.parameters()).device

    for i, d in enumerate(train_dataloader):
        d = d.to(device)
        print (d)

        optimizer.zero_grad()
        aux_optimizer.zero_grad()

        out_net = model(d)

        out_criterion = criterion(out_net, d)
        print (" LOSSS " , out_criterion["bpp_loss"])
        out_criterion["loss"].backward()
        # TRY TO GET A CRASH HERE WITHOUT THE CLIPPING
        #if clip_max_norm > 0:
        #    torch.nn.utils.clip_grad_norm_(model.parameters(), clip_max_norm)
        optimizer.step()

        aux_loss = model.aux_loss()
        aux_loss.backward()
        aux_optimizer.step()
        writer.write_metric("psnr", psnr(out_criterion["mse_loss"].item()), int(i+ epoch*(len(train_dataloader.dataset)/train_dataloader.batch_size)))
        writer.write_metric("mse", out_criterion["mse_loss"].item(), int(i+ epoch*(len(train_dataloader.dataset)/train_dataloader.batch_size)))
        writer.write_metric(f"bpp", out_criterion["bpp_loss"].item(),int( i+ epoch*(len(train_dataloader.dataset)/train_dataloader.batch_size)))
        writer.write_metric(f"aux loss", aux_loss.item(), int(i+ epoch*(len(train_dataloader.dataset)/train_dataloader.batch_size)))

        if i % 10 == 0:
            print(
                f"Train epoch {epoch}: ["
                f"{i*len(d)}/{len(train_dataloader.dataset)}"
                f" ({100. * i / len(train_dataloader):.0f}%)]"
                f'\tLoss: {out_criterion["loss"].item():.3f} |'
                f'\tMSE loss: {out_criterion["mse_loss"].item():.3f} |'
                f'\tPSNR loss: {psnr(out_criterion["mse_loss"].item()):.3f} |'
                f'\tBpp loss: {out_criterion["bpp_loss"].item():.2f} |'
                f"\tAux loss: {aux_loss.item():.2f}"
            )
    return int(i+ epoch*(len(train_dataloader.dataset)/train_dataloader.batch_size))


def test_epoch(epoch, test_dataloader, model, criterion, writer, last_iteration):
    model.eval()
    device = next(model.parameters()).device

    loss = AverageMeter()
    bpp_loss = AverageMeter()
    mse_loss = AverageMeter()
    aux_loss = AverageMeter()

    with torch.no_grad():
        for d in test_dataloader:
            d = d.to(device)
            out_net = model(d)
            out_criterion = criterion(out_net, d)

            aux_loss.update(model.aux_loss())
            bpp_loss.update(out_criterion["bpp_loss"])
            loss.update(out_criterion["loss"])
            mse_loss.update(out_criterion["mse_loss"])

    writer.write_metric("val/psnr", psnr(mse_loss.avg), last_iteration)
    writer.write_metric("val/mse", mse_loss.avg, last_iteration)
    writer.write_metric(f"val/bpp",bpp_loss.avg, last_iteration)
    writer.write_metric(f"val/aux loss", aux_loss.avg, last_iteration)

    print(
        f"Test epoch {epoch}: Average losses:"
        f"\tLoss: {loss.avg:.3f} |"
        f"\tMSE loss: {mse_loss.avg:.3f} |"
        f"\tBpp loss: {bpp_loss.avg:.2f} |"
        f"\tAux loss: {aux_loss.avg:.2f}\n"
    )

    return loss.avg


def save_checkpoint(state, is_best, filename="checkpoint.pth.tar"):
    torch.save(state, filename)
    if is_best:
        shutil.copyfile(filename, "checkpoint_best_loss.pth.tar")


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Example training script")
    # yapf: disable
    parser.add_argument(
        '-d',
        '--dataset',
        type=str,
        required=True,
        help='Training dataset')
    parser.add_argument(
        '-e',
        '--epochs',
        default=100,
        type=int,
        help='Number of epochs (default: %(default)s)')
    parser.add_argument(
        '-lr',
        '--learning-rate',
        default=1e-4,
        type=float,
        help='Learning rate (default: %(default)s)')
    parser.add_argument(
        '-n',
        '--num-workers',
        type=int,
        default=3,
        help='Dataloaders threads (default: %(default)s)')
    parser.add_argument(
        '--lambda',
        dest='lmbda',
        type=float,
        default=1e-2,
        help='Bit-rate distortion parameter (default: %(default)s)')
    parser.add_argument(
        '--batch-size',
        type=int,
        default=16,
        help='Batch size (default: %(default)s)')
    parser.add_argument(
        '--test-batch-size',
        type=int,
        default=64,
        help='Test batch size (default: %(default)s)')
    parser.add_argument(
        '--aux-learning-rate',
        default=1e-3,
        help='Auxiliary loss learning rate (default: %(default)s)')
    parser.add_argument(
        '--patch-size',
        type=int,
        nargs=2,
        default=(64, 64),
        help='Size of the patches to be cropped (default: %(default)s)')
    parser.add_argument(
        '--experiment',
        '-exp',
        help='The experiment path')
    parser.add_argument(
        '--cuda',
        action='store_true',
        help='Use cuda')
    parser.add_argument(
        '--save',
        action='store_true',
        help='Save model to disk')
    parser.add_argument(
        '--seed',
        type=float,
        help='Set random seed for reproducibility')
    parser.add_argument('--clip_max_norm',
                        default=1.0,
                        type=float,
                        help='gradient clipping max norm')
    # yapf: enable
    args = parser.parse_args(argv)
    return args


def main(argv):
    args = parse_args(argv)

    if args.seed is not None:
        torch.manual_seed(args.seed)
        random.seed(args.seed)

    train_transforms = transforms.Compose(
        [transforms.RandomCrop(args.patch_size), transforms.ToTensor()]
    )

    test_transforms = transforms.Compose(
        [transforms.CenterCrop(args.patch_size), transforms.ToTensor()]
    )
    print ("INdexing image folder \n")
    train_dataset = ImageFolder(args.dataset, split="train", transform=train_transforms)
    test_dataset = ImageFolder(args.dataset, split="test", transform=test_transforms)

    train_dataloader = DataLoader(
        train_dataset,
        batch_size=args.batch_size,
        num_workers=args.num_workers,
        shuffle=True,
        pin_memory=True,
    )
    if not os.path.exists('_logs'):
        os.mkdir('_logs')
    if not os.path.exists(os.path.join('_logs',args.experiment)):
        os.mkdir(os.path.join('_logs',args.experiment))

    test_dataloader = DataLoader(
        test_dataset,
        batch_size=args.test_batch_size,
        num_workers=args.num_workers,
        shuffle=False,
        pin_memory=True,
    )

    device = "cuda" if args.cuda and torch.cuda.is_available() else "cpu"

    net = Cheng2020Attention()
    net = net.to(device)

    if args.cuda and torch.cuda.device_count() > 1:
        net = CustomDataParallel(net)

    writer = get_writer(True, experiment_path=os.path.join('_logs',args.experiment), config=vars(args), dummy=False)

    optimizer, aux_optimizer = configure_optimizers(net, args)
    criterion = RateDistortionLoss(lmbda=args.lmbda)

    best_loss = 1e10
    for epoch in range(args.epochs):
        last_iteration = train_one_epoch(
            net,
            criterion,
            train_dataloader,
            optimizer,
            aux_optimizer,
            epoch,
            args.clip_max_norm,
            writer
        )

        loss = test_epoch(epoch, test_dataloader, net, criterion, writer, last_iteration)

        is_best = loss < best_loss
        best_loss = min(loss, best_loss)
        if args.save:
            save_checkpoint(
                {
                    "epoch": epoch + 1,
                    "state_dict": net.state_dict(),
                    "loss": loss,
                    "optimizer": optimizer.state_dict(),
                    "aux_optimizer": aux_optimizer.state_dict(),
                },
                is_best,
            )


if __name__ == "__main__":
    main(sys.argv[1:])