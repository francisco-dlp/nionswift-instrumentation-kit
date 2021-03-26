import contextlib
import numpy
import typing
import unittest

from nion.data import Calibration
from nion.data import DataAndMetadata
from nion.instrumentation import Acquisition


class ScanDataStream(Acquisition.DataStream):
    """Provide a data stream for one scan with the given channel.

    frame_count is the number of frames to generate.

    scan_shape is the shape of each frame.

    channels are the list of channels to generate.

    partial_length is the size of each chunk of data (number of samples) to send at once.
    """
    def __init__(self, frame_count: int, scan_shape: Acquisition.ShapeType, channels: typing.Sequence[Acquisition.Channel], partial_length: int):
        super().__init__(frame_count)
        # frame counts are used for allocating and returning test data
        self.__frame_count = frame_count
        self.__frame_index = 0
        # scan length is total samples in scan shape
        self.__scan_shape = scan_shape
        self.__scan_length = numpy.product(scan_shape)
        # channels
        self.__channels = tuple(channels)
        # partial length is the size of each chunk sent. partial index is the next sample to be sent.
        self.__partial_length = partial_length
        self.__partial_index = 0
        self.data = {channel: numpy.random.randn(self.__frame_count, self.__scan_length) for channel in channels}

    @property
    def channels(self) -> typing.Tuple[Acquisition.Channel, ...]:
        return self.__channels

    def _send_next(self) -> None:
        assert self.__frame_index < self.__frame_count
        assert self.__partial_index < self.__scan_length
        # data metadata describes the data being sent from this stream: shape, data type, and descriptor
        data_descriptor = DataAndMetadata.DataDescriptor(False, 0, 0)
        data_metadata = DataAndMetadata.DataMetadata(((), float), data_descriptor=data_descriptor)
        # update the index to be used in the data slice
        start_index = self.__partial_index
        stop_index = min(start_index + self.__partial_length, self.__scan_length)
        new_count = stop_index - start_index
        # source data slice is relative to data start/stop
        source_data_slice = (slice(start_index, stop_index),)
        state = Acquisition.DataStreamStateEnum.PARTIAL if stop_index < self.__scan_length else Acquisition.DataStreamStateEnum.COMPLETE
        for channel in self.channels:
            data_stream_event = Acquisition.DataStreamEventArgs(self, channel, data_metadata,
                                                                self.data[channel][self.__frame_index],
                                                                new_count, source_data_slice, state)
            self.data_available_event.fire(data_stream_event)
        # update indexes
        if state == Acquisition.DataStreamStateEnum.COMPLETE:
            self.__partial_index = 0
            self.__frame_index = self.__frame_index + 1
            self._sequence_next()
        else:
            self.__partial_index = stop_index


class FrameDataStream(Acquisition.DataStream):
    """Provide a single data stream frame by frame.

    frame_count is the number of frames to generate.

    frame_shape is the shape of each frame.

    channel is the channel on which to send the data.

    partial_height is the size of each chunk of data (number of samples) to send at once.
    """
    def __init__(self, frame_count: int, frame_shape: Acquisition.ShapeType, channel: Acquisition.Channel, partial_height: typing.Optional[int] = None):
        super().__init__(frame_count)
        assert len(frame_shape) == 2
        # frame counts are used for allocating and returning test data
        self.__frame_count = frame_count
        self.__frame_index = 0
        # frame shape and channel
        self.__frame_shape = tuple(frame_shape)
        self.__channel = channel
        # partial height is the size of each chunk sent. partial index is the next sample to be sent.
        self.__partial_height = partial_height or frame_shape[0]
        self.__partial_index = 0
        self.data = numpy.random.randn(self.__frame_count, *self.__frame_shape)

    @property
    def channels(self) -> typing.Tuple[Acquisition.Channel, ...]:
        return (self.__channel,)

    def _send_next(self) -> None:
        assert self.__frame_index < self.__frame_count
        # data metadata describes the data being sent from this stream: shape, data type, and descriptor
        data_descriptor = DataAndMetadata.DataDescriptor(False, 0, len(self.__frame_shape))
        data_metadata = DataAndMetadata.DataMetadata((self.__frame_shape, float), data_descriptor=data_descriptor)
        # update the index to be used in the data slice
        new_partial = min(self.__partial_index + self.__partial_height, self.__frame_shape[0])
        source_data_slice = (slice(self.__partial_index, new_partial), slice(None))
        # send the data with no count. this is required when using partial.
        state = Acquisition.DataStreamStateEnum.PARTIAL if new_partial < self.__frame_shape[0] else Acquisition.DataStreamStateEnum.COMPLETE
        data_stream_event = Acquisition.DataStreamEventArgs(self, self.__channel, data_metadata,
                                                            self.data[self.__frame_index], None,
                                                            source_data_slice, state)
        self.data_available_event.fire(data_stream_event)
        if state == Acquisition.DataStreamStateEnum.PARTIAL:
            self.__partial_index = new_partial
        else:
            self.__state = Acquisition.DataStreamStateEnum.COMPLETE
            self.__partial_index = 0
            self.__frame_index += 1
            self._sequence_next()


class TestAcquisitionClass(unittest.TestCase):

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_camera_sequence_acquisition(self):
        sequence_len = 4
        data_stream = FrameDataStream(sequence_len, (2, 2), 0)
        sequencer = Acquisition.SequenceDataStream(data_stream, sequence_len)
        maker = Acquisition.DataStreamToDataAndMetadata(sequencer)
        with maker.ref():
            while not maker.is_finished:
                maker.send_next()
            self.assertTrue(numpy.array_equal(data_stream.data, maker.get_data(0).data))

    def test_camera_collection_acquisition(self):
        # in this case the collector is acting only to arrange the data, not to provide any scan
        collection_shape = (4, 3)
        data_stream = FrameDataStream(numpy.product(collection_shape), (2, 2), 0)
        collector = Acquisition.CollectedDataStream(data_stream, collection_shape,
                                                    [Calibration.Calibration(), Calibration.Calibration()])
        maker = Acquisition.DataStreamToDataAndMetadata(collector)
        with maker.ref():
            while not maker.is_finished:
                maker.send_next()
            expected_shape = collection_shape + maker.get_data(0).data.shape[-len(collection_shape):]
            self.assertTrue(numpy.array_equal(data_stream.data.reshape(expected_shape), maker.get_data(0).data))

    def test_scan_sequence_acquisition(self):
        sequence_len = 4
        data_stream = FrameDataStream(sequence_len, (4, 4), 0, 2)
        sequencer = Acquisition.SequenceDataStream(data_stream, sequence_len)
        maker = Acquisition.DataStreamToDataAndMetadata(sequencer)
        with maker.ref():
            while not maker.is_finished:
                maker.send_next()
            self.assertTrue(numpy.array_equal(data_stream.data, maker.get_data(0).data))

    def test_scan_collection_acquisition(self):
        # in this case the collector is acting only to arrange the data, not to provide any scan.
        # the scan data is hard coded to produce a scan.
        collection_shape = (5, 3)
        data_stream = FrameDataStream(numpy.product(collection_shape), (4, 4), 0, 2)
        collector = Acquisition.CollectedDataStream(data_stream, collection_shape,
                                                    [Calibration.Calibration(), Calibration.Calibration()])
        maker = Acquisition.DataStreamToDataAndMetadata(collector)
        with maker.ref():
            while not maker.is_finished:
                maker.send_next()
            expected_shape = collection_shape + maker.get_data(0).data.shape[-len(collection_shape):]
            self.assertTrue(numpy.array_equal(data_stream.data.reshape(expected_shape), maker.get_data(0).data))

    def test_scan_as_collection(self):
        # scan will produce a data stream of pixels.
        # the collection must make it into an image.
        scan_shape = (8, 8)
        data_stream = ScanDataStream(1, scan_shape, [0], scan_shape[1])
        collector = Acquisition.CollectedDataStream(data_stream, scan_shape, [Calibration.Calibration(), Calibration.Calibration()])
        maker = Acquisition.DataStreamToDataAndMetadata(collector)
        with maker.ref():
            while not maker.is_finished:
                maker.send_next()
            expected_shape = scan_shape
            self.assertTrue(numpy.array_equal(data_stream.data[0].reshape(expected_shape), maker.get_data(0).data))
            self.assertEqual(DataAndMetadata.DataDescriptor(False, 0, 2), maker.get_data(0).data_descriptor)

    def test_scan_as_collection_as_sequence(self):
        # scan will produce a data stream of pixels.
        # the collection must make it into an image.
        # that will be collected to a sequence.
        sequence_len = 4
        scan_shape = (8, 8)
        data_stream = ScanDataStream(sequence_len, scan_shape, [0], scan_shape[1])
        collector = Acquisition.CollectedDataStream(data_stream, scan_shape, [Calibration.Calibration(), Calibration.Calibration()])
        sequencer = Acquisition.SequenceDataStream(collector, sequence_len)
        maker = Acquisition.DataStreamToDataAndMetadata(sequencer)
        with maker.ref():
            while not maker.is_finished:
                maker.send_next()
            expected_shape = (sequence_len,) + scan_shape
            self.assertTrue(numpy.array_equal(data_stream.data[0].reshape(expected_shape), maker.get_data(0).data))
            self.assertEqual(DataAndMetadata.DataDescriptor(True, 0, 2), maker.get_data(0).data_descriptor)

    def test_scan_as_collection_two_channels(self):
        # scan will produce two data streams of pixels.
        # the collection must make it into two images.
        scan_shape = (8, 8)
        data_stream = ScanDataStream(1, scan_shape, [0, 1], scan_shape[1])
        collector = Acquisition.CollectedDataStream(data_stream, scan_shape, [Calibration.Calibration(), Calibration.Calibration()])
        maker = Acquisition.DataStreamToDataAndMetadata(collector)
        with maker.ref():
            while not maker.is_finished:
                maker.send_next()
            expected_shape = scan_shape
            self.assertTrue(numpy.array_equal(data_stream.data[0].reshape(expected_shape), maker.get_data(0).data))
            self.assertTrue(numpy.array_equal(data_stream.data[1].reshape(expected_shape), maker.get_data(1).data))
            self.assertEqual(DataAndMetadata.DataDescriptor(False, 0, 2), maker.get_data(0).data_descriptor)
            self.assertEqual(DataAndMetadata.DataDescriptor(False, 0, 2), maker.get_data(1).data_descriptor)

    def test_scan_as_collection_two_channels_and_camera_summed_vertically(self):
        # scan will produce two data streams of pixels.
        # camera will produce one stream of frames.
        # the sequence must make it into two images and a sequence of images.
        scan_shape = (8, 8)
        scan_data_stream = ScanDataStream(1, scan_shape, [0, 1], scan_shape[1])
        camera_data_stream = FrameDataStream(1 * numpy.product(scan_shape), (2, 2), 2)
        summed_data_stream = Acquisition.SummedDataStream(camera_data_stream, axis=0)
        combined_data_stream = Acquisition.CombinedDataStream([scan_data_stream, summed_data_stream])
        collector = Acquisition.CollectedDataStream(combined_data_stream, scan_shape, [Calibration.Calibration(), Calibration.Calibration()])
        maker = Acquisition.DataStreamToDataAndMetadata(collector)
        with maker.ref():
            while not maker.is_finished:
                maker.send_next()
            expected_scan_shape = scan_shape
            expected_camera_shape = scan_shape + (2,)
            self.assertTrue(numpy.array_equal(scan_data_stream.data[0].reshape(expected_scan_shape), maker.get_data(0).data))
            self.assertTrue(numpy.array_equal(scan_data_stream.data[1].reshape(expected_scan_shape), maker.get_data(1).data))
            self.assertTrue(numpy.array_equal(camera_data_stream.data.sum(-2).reshape(expected_camera_shape), maker.get_data(2).data))
            self.assertEqual(DataAndMetadata.DataDescriptor(False, 0, 2), maker.get_data(0).data_descriptor)
            self.assertEqual(DataAndMetadata.DataDescriptor(False, 0, 2), maker.get_data(1).data_descriptor)
            self.assertEqual(DataAndMetadata.DataDescriptor(False, 2, 1), maker.get_data(2).data_descriptor)

    def test_scan_as_collection_camera_summed_to_scalar(self):
        # scan will produce two data streams of pixels.
        # camera will produce one stream of frames.
        # the sequence must make it into two images and a sequence of images.
        scan_shape = (8, 8)
        camera_data_stream = FrameDataStream(1 * numpy.product(scan_shape), (2, 2), 2)
        summed_data_stream = Acquisition.SummedDataStream(camera_data_stream)
        collector = Acquisition.CollectedDataStream(summed_data_stream, scan_shape, [Calibration.Calibration(), Calibration.Calibration()])
        maker = Acquisition.DataStreamToDataAndMetadata(collector)
        with maker.ref():
            while not maker.is_finished:
                maker.send_next()
            expected_camera_shape = scan_shape
            self.assertTrue(numpy.array_equal(camera_data_stream.data.sum((-2, -1)).reshape(expected_camera_shape), maker.get_data(2).data))
            self.assertEqual(DataAndMetadata.DataDescriptor(False, 0, 2), maker.get_data(2).data_descriptor)

    def test_scan_as_collection_two_channels_and_camera_summed_to_scalar(self):
        # scan will produce two data streams of pixels.
        # camera will produce one stream of frames.
        # the sequence must make it into two images and a sequence of images.
        scan_shape = (8, 8)
        scan_data_stream = ScanDataStream(1, scan_shape, [0, 1], scan_shape[1])
        camera_data_stream = FrameDataStream(1 * numpy.product(scan_shape), (2, 2), 2)
        summed_data_stream = Acquisition.SummedDataStream(camera_data_stream)
        combined_data_stream = Acquisition.CombinedDataStream([scan_data_stream, summed_data_stream])
        collector = Acquisition.CollectedDataStream(combined_data_stream, scan_shape, [Calibration.Calibration(), Calibration.Calibration()])
        maker = Acquisition.DataStreamToDataAndMetadata(collector)
        with maker.ref():
            while not maker.is_finished:
                maker.send_next()
            expected_scan_shape = scan_shape
            expected_camera_shape = scan_shape
            self.assertTrue(numpy.array_equal(scan_data_stream.data[0].reshape(expected_scan_shape), maker.get_data(0).data))
            self.assertTrue(numpy.array_equal(scan_data_stream.data[1].reshape(expected_scan_shape), maker.get_data(1).data))
            self.assertTrue(numpy.array_equal(camera_data_stream.data.sum((-2, -1)).reshape(expected_camera_shape), maker.get_data(2).data))
            self.assertEqual(DataAndMetadata.DataDescriptor(False, 0, 2), maker.get_data(0).data_descriptor)
            self.assertEqual(DataAndMetadata.DataDescriptor(False, 0, 2), maker.get_data(1).data_descriptor)
            self.assertEqual(DataAndMetadata.DataDescriptor(False, 0, 2), maker.get_data(2).data_descriptor)

    def test_sequence_of_scan_as_collection_two_channels_and_camera(self):
        # scan will produce two data streams of pixels.
        # camera will produce one stream of frames.
        # the sequence must make it into two images and a sequence of images.
        sequence_len = 4
        scan_shape = (8, 8)
        scan_data_stream = ScanDataStream(sequence_len, scan_shape, [0, 1], scan_shape[1])
        camera_data_stream = FrameDataStream(sequence_len * numpy.product(scan_shape), (2, 2), 2)
        combined_data_stream = Acquisition.CombinedDataStream([scan_data_stream, camera_data_stream])
        collector = Acquisition.CollectedDataStream(combined_data_stream, scan_shape, [Calibration.Calibration(), Calibration.Calibration()])
        sequencer = Acquisition.SequenceDataStream(collector, sequence_len)
        maker = Acquisition.DataStreamToDataAndMetadata(sequencer)
        with maker.ref():
            while not maker.is_finished:
                maker.send_next()
            expected_scan_shape = (sequence_len,) + scan_shape
            expected_camera_shape = (sequence_len,) + scan_shape + (2, 2)
            self.assertTrue(numpy.array_equal(scan_data_stream.data[0].reshape(expected_scan_shape), maker.get_data(0).data))
            self.assertTrue(numpy.array_equal(scan_data_stream.data[1].reshape(expected_scan_shape), maker.get_data(1).data))
            self.assertTrue(numpy.array_equal(camera_data_stream.data.reshape(expected_camera_shape), maker.get_data(2).data))
            self.assertEqual(DataAndMetadata.DataDescriptor(True, 0, 2), maker.get_data(0).data_descriptor)
            self.assertEqual(DataAndMetadata.DataDescriptor(True, 0, 2), maker.get_data(1).data_descriptor)
            self.assertEqual(DataAndMetadata.DataDescriptor(True, 2, 2), maker.get_data(2).data_descriptor)
