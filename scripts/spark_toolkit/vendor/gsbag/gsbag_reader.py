#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import collections
import importlib
import os
import sys
from gsbag.gsbag_types import *
import json

# Refer to the _adigo_record_wrapper.so with relative path so that it can be
# always addressed as a part of the runfiles.
wrapper_lib_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(wrapper_lib_path)

_ADIGO = importlib.import_module('gsbag_reader_wrapper')


class GsBagReader(object):
    def __init__(self, file_name):
        self.adigo_reader = _ADIGO.new_PyGsBagReader(file_name)

    def __del__(self):
        # _ADIGO.delete_PyGsBagReader(self.adigo_reader)
        if self.adigo_reader is not None:
            try:
                _ADIGO.delete_PyGsBagReader(self.adigo_reader)
            except Exception as e:

                print(f"Error deleting GsBagReader: {e}")
            finally:
                self.reader = None


    def read_messages(self):
        while True:
            message = _ADIGO.PyGsBagReader_ReadMessage(self.adigo_reader)
            meta_data_str = message["meta_data"] or "{}"
            meta_data_ = json.loads(meta_data_str)

            if not message["end"]:
                yield BagMessage(
                    message["timestamp"], 
                    message["pub_timestamp"],
                    message["topic_name"], 
                    message["topic_type"],
                    message["data"], 
                    message["data_size_str"],  
                    meta_data_
                )
            else:
                # print "No message more."
                break

    def get_message_type(self, channel_name):
        return _ADIGO.PyGsBagReader_GetMessageType(
            self.adigo_reader, channel_name).decode('utf-8')

    def set_topic_filter(self, topic_filters):
        _ADIGO.PyGsBagReader_SetTopicFilter(self.adigo_reader, topic_filters)

    def get_topics(self):
        return _ADIGO.PyGsBagReader_GetTopics(self.adigo_reader)

    def get_bag_meta(self):
        meta =  _ADIGO.PyGsBagReader_GetBagMeta(self.adigo_reader)
        return BagMeta(
            meta['bag_size'], meta['duration'], 
            meta['start_timestamp'], meta['end_timestamp'],
            meta['message_count']
        )

    def get_bag_info(self):
        '''返回metadata.yaml内容。bytes类型的yaml内容'''
        bag_info = _ADIGO.PyGsBagReader_GetBagInfo(self.adigo_reader)
        return bag_info

    def get_topic_meta(self, topic):
        meta = _ADIGO.PyGsBagReader_GetTopicMeta(self.adigo_reader, topic)
        return TopicMeta(
            meta['name'], meta['type'], meta['serialization_format'], meta['message_count']
        )


class HobotMessageSerializer:
    @staticmethod
    def create_topic_meta(hobot_message, topic_name, topic_type=None):
        return TopicMeta(
            name=topic_name,
            type=topic_type if topic_type is not None else hobot_message.DESCRIPTOR.full_name,
            serialization_format='hobot_python',
            message_count=0
        )
    
    @staticmethod
    def serialize(timestamp, pub_timestamp, topic_name, topic_type, hobot_message):
        serial_data=hobot_message.SerializeToString()
        return BagMessage(
            timestamp=timestamp,
            pub_timestamp=pub_timestamp,
            topic_name=topic_name,
            topic_type=topic_type,
            data=serial_data,
            data_size_str=str(len(serial_data)),
            meta_data={}
        )
    
    @staticmethod
    def serialize_image(timestamp, pub_timestamp, topic_name, image_message, image_data):
        image_meta = image_message.SerializeToString()
        return BagMessage(
            timestamp=timestamp,
            pub_timestamp=pub_timestamp,
            topic_name=topic_name,
            topic_type='',
            data=image_meta + b''.join(image_data),
            data_size_str=','.join( [str(len(image_meta))] + [str(len(v)) for v in image_data]),
            meta_data={}
        )

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

