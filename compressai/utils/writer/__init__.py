
from compressai.utils.writer.dummy import DummyWriter
from compressai.utils.writer.wandb import WandbWriter


def get_writer(offline, experiment_path, config=None, dummy=False):
    if dummy:
        return DummyWriter()
    return WandbWriter(offline=offline, experiment_path=experiment_path, config=config)