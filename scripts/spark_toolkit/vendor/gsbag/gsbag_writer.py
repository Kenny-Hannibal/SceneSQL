#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import collections
import importlib
import os
import sys
import json

# Refer to the _adigo_record_wrapper.so with relative path so that it can be
# always addressed as a part of the runfiles.
wrapper_lib_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(wrapper_lib_path)

_ADIGO = importlib.import_module('gsbag_writer_wrapper')

class GsBagWriter(object):
    def __init__(self, bag_path, storage_version="2", max_cache_size=100, split_topics_list={}, compressed_method="none"):
        topic_lists_str = json.dumps(split_topics_list).encode("utf-8")
        self.adigo_writer = _ADIGO.new_PyGsBagWriter(bag_path, storage_version, max_cache_size, topic_lists_str, compressed_method)

    def __del__(self):
        _ADIGO.delete_PyGsBagWriter(self.adigo_writer)

    # def write_message(self, timestamp, topic_name, topic_type, serialization_format, serialized_data, serialized_data_sizes_str=None):
    def write_message(self, *args):
        if len(args) in (5, 6):
            timestamp, topic_name, topic_type, serialization_format, serialized_data = args[:5]
            serialized_data_sizes_str = args[5] if len(args) == 6 else None
    
            _ADIGO.PyGsBagWriter_CreateTopic(self.adigo_writer, topic_name, topic_type, serialization_format)

            if serialized_data_sizes_str is None:
                serialized_data_sizes_str_v2 = str(len(serialized_data))
            else:
                serialized_data_sizes_str_v2 = serialized_data_sizes_str
            _ADIGO.PyGsBagWriter_WriteMessage(self.adigo_writer, timestamp, topic_name, topic_type, serialized_data, serialized_data_sizes_str_v2)
        elif len(args) == 2:
            topic_meta, bag_message = args[:]

            _ADIGO.PyGsBagWriter_CreateTopic(self.adigo_writer, topic_meta.name, topic_meta.type, topic_meta.serialization_format)
            
            meta_data_str = json.dumps(bag_message.meta_data).encode("utf-8")

            if bag_message.data_size_str is None:
                print('len = ', len(bag_message.data))
                data_size_str_ = str(len(bag_message.data))
            else:
                data_size_str_ = bag_message.data_size_str

            _ADIGO.PyGsBagWriter_WriteBagMessage(self.adigo_writer, bag_message.timestamp, bag_message.pub_timestamp, bag_message.topic_name, bag_message.topic_type, \
                                                                        bag_message.data, data_size_str_, meta_data_str)
        else:
            print("write message error, please check args !!!")


class HobotMessageSerializer:
    @staticmethod
    def deserialize(bag_message, hobot_message):
        hobot_message.ParseFromString(bag_message.data)

    @staticmethod
    def deserialize_image(bag_message, image_message, image_data=[]):
        size_list = map(lambda x: int(x), bag_message.data_size_str.split(','))

        start_idx = 0
        for i, sz in enumerate(size_list):
            if i == 0:
                image_message.ParseFromString(bag_message.data[:sz])
            else:
                image_data.append(bag_message.data[start_idx:sz+start_idx])
            start_idx += sz

