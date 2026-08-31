#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import collections


BagMessage = collections.namedtuple('BagMessage', 'timestamp pub_timestamp topic_name topic_type data data_size_str meta_data')
BagMeta = collections.namedtuple('BagMeta','bag_size duration start_timestamp end_timestamp message_count')
TopicMeta = collections.namedtuple('TopicMeta','name type serialization_format message_count')
