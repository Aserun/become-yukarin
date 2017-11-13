from functools import partial
from pathlib import Path
from typing import Optional

import chainer
import numpy
import pysptk
import pyworld

from become_yukarin.config import Config
from become_yukarin.data_struct import AcousticFeature
from become_yukarin.data_struct import Wave
from become_yukarin.dataset.dataset import AcousticFeatureDenormalizeProcess
from become_yukarin.dataset.dataset import AcousticFeatureLoadProcess
from become_yukarin.dataset.dataset import AcousticFeatureNormalizeProcess
from become_yukarin.dataset.dataset import AcousticFeatureProcess
from become_yukarin.dataset.dataset import EncodeFeatureProcess
from become_yukarin.dataset.dataset import DecodeFeatureProcess
from become_yukarin.dataset.dataset import WaveFileLoadProcess
from become_yukarin.model import create as create_model


class VoiceChanger(object):
    def __init__(self, config: Config, model_path: Path):
        self.config = config
        self.model_path = model_path

        self.model = model = create_model(config.model)
        chainer.serializers.load_npz(str(model_path), model)

        self._param = param = config.dataset.param
        self._wave_process = WaveFileLoadProcess(
            sample_rate=param.voice_param.sample_rate,
            top_db=param.voice_param.top_db,
        )
        self._feature_process = AcousticFeatureProcess(
            frame_period=param.acoustic_feature_param.frame_period,
            order=param.acoustic_feature_param.order,
            alpha=param.acoustic_feature_param.alpha,
        )

        _acoustic_feature_load_process = AcousticFeatureLoadProcess()

        input_mean = _acoustic_feature_load_process(config.dataset.input_mean_path, test=True)
        input_var = _acoustic_feature_load_process(config.dataset.input_var_path, test=True)
        target_mean = _acoustic_feature_load_process(config.dataset.target_mean_path, test=True)
        target_var = _acoustic_feature_load_process(config.dataset.target_var_path, test=True)
        self._feature_normalize = AcousticFeatureNormalizeProcess(
            mean=input_mean,
            var=input_var,
        )
        self._feature_denormalize = AcousticFeatureDenormalizeProcess(
            mean=target_mean,
            var=target_var,
        )

        self._encode_feature = EncodeFeatureProcess(['mfcc'])
        self._decode_feature = DecodeFeatureProcess(['mfcc'])

    def __call__(self, voice_path: Path, out_sampling_rate: Optional[int] = None):
        input = input_wave = self._wave_process(str(voice_path), test=True)
        if out_sampling_rate is None:
            out_sampling_rate = input_wave.sampling_rate

        input = input_feature = self._feature_process(input, test=True)
        input = self._feature_normalize(input, test=True)
        input = self._encode_feature(input, test=True)

        converter = partial(chainer.dataset.convert.concat_examples, padding=0)
        inputs = converter([input])

        with chainer.using_config('train', False):
            out = self.model(inputs).data[0]

        out = self._decode_feature(out, test=True)
        out = self._feature_denormalize(out, test=True)

        fftlen = pyworld.get_cheaptrick_fft_size(input_wave.sampling_rate)
        spectrogram = pysptk.mc2sp(
            out.mfcc,
            alpha=self._param.acoustic_feature_param.alpha,
            fftlen=fftlen,
        )

        out = AcousticFeature(
            f0=input_feature.f0,
            spectrogram=spectrogram,
            aperiodicity=input_feature.aperiodicity,
            mfcc=out.mfcc,
        ).astype(numpy.float64)
        out = pyworld.synthesize(
            f0=out.f0,
            spectrogram=out.spectrogram,
            aperiodicity=out.aperiodicity,
            fs=out_sampling_rate,
            frame_period=self._param.acoustic_feature_param.frame_period,
        )

        return Wave(out, sampling_rate=out_sampling_rate)
