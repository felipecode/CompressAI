import os

import wandb

from compressai.utils.writer.writer import Writer


class WandbWriter(Writer):
    def __init__(self, config, experiment_path, offline=False):
        super(WandbWriter, self).__init__(offline)
        if offline:
            os.environ["WANDB_MODE"] = "dryrun"
        wandb.init(
            project="dif",
            name=config["experiment"],
            dir=experiment_path,
            config=config
        )

    def write(self):
        pass

    def watch_all(self, net):
        wandb.watch(net, log='all')

    def write_metric(self, name, value, iter):
        wandb.log({name:value}, step=iter)

    def write_image(self, name, value, iter):
        wandb.log({name: wandb.Image(value)}, step=iter)

    def write_parameters(self, params):
        pass

    def close(self):
        wandb.run.finish()