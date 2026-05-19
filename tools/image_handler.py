import os
import av
import json
import subprocess
import numpy as np
import logging
from collections import defaultdict
from threading import Thread
from io import BytesIO


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
LOGGER = logging.getLogger(__name__)
LOGGER.info("init logger")

class ImageType:
    RGB24 = "rgb24"
    BGR24 = "bgr24"
    NV12 = "yuv420p"

class HevcDecoder():
    
    @classmethod
    def decode_frames(cls, width:int, height:int, hevc_raw_bytes:bytes) -> list:
        ffmpeg_command = [
            "ffmpeg",
            "-loglevel",
            "info", 
            "-i",
            "-",  # 从标准输入读取
            "-c:v",
            "libx265",
            "-s",
            f"{width}x{height}",  
            "-f",
            "image2pipe",
            "-pix_fmt",
            ImageType.RGB24, 
            "-vcodec",
            "rawvideo",
            "-", 
        ]
        ffmpeg_process = subprocess.Popen(
            ffmpeg_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )
    
        ffmpeg_process.stdin.write(hevc_raw_bytes)
        ffmpeg_process.stdin.close()

        frames = []
        while True:
            # read as one rgb file
            raw_frame = ffmpeg_process.stdout.read(width * height * 3)
            if not raw_frame:
                break
            frame = np.frombuffer(raw_frame, dtype=np.uint8).reshape((height, width, 3))
            frames.append(frame)
          
        ffmpeg_process.wait() 
        return frames
    

    @classmethod
    def decode_frames_v2(cls, hevc_bytes_io:BytesIO) -> list:      
        frame_list = []
        container = av.open(hevc_bytes_io, mode='r', format='hevc')

        for packet in container.demux(video=0):
            try:
                for frame in packet.decode():
                    frame_list.append(frame)
            except Exception as e:
                LOGGER.error(
                    f"Extract current stream: encounter error: {e}")
        return frame_list


class ImageHandler:

    def __init__(self, workspace:str, collect_date:int, vehicle_name:str, bag_type:str, bag_id:str) -> None:
        self.workspace = workspace
        self.collect_date = collect_date
        self.vehicle_name = vehicle_name
        self.bag_type = bag_type
        self.bag_id = bag_id
        self.channel_bytes = {}
        self.channel_meta = {}
        self.uploaded = []
        self.uploaded_topic = []
        self.channel_timestamp = defaultdict(list)
        self.local_channel_file_path = defaultdict(list)
        self.frame_count = 0

    @staticmethod
    def reformat_topic_name(topic_name: str) -> str:
        """
        reformat topic name '/a/b/c' to 'a_b_c'
        """
        if topic_name.startswith("/"):
            topic_name = topic_name[1:]
        return topic_name.strip("/").replace("/", "_")
    
    def get_topic_file_folder(self, channel:str) -> str:
        folder_path = os.path.join(self.workspace, "parsed", channel)
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
        return folder_path

    def add_hevc_unit(self, topic_name, image_hevc_unit):
        channel = self.reformat_topic_name(topic_name)
        timestamp = image_hevc_unit[0]
        img_width = image_hevc_unit[1]
        img_height = image_hevc_unit[2]
        raw_bytes = image_hevc_unit[3]
        self.channel_meta[channel] = {
            "width": img_width,
            "height": img_height
        }
        if channel not in self.channel_bytes:
            self.channel_bytes[channel] = BytesIO()
        self.channel_bytes[channel].write(raw_bytes)
        self.channel_timestamp[channel].append(timestamp)
        self.frame_count += 1


    def serial_process_channel(self, output_dir):


        for channel, meta in self.channel_meta.items():

            timestamps = self.channel_timestamp[channel]
            hevc = self.channel_bytes[channel]
            channel_name = channel
            width = meta["width"]
            height = meta["height"]

            channel_img_folder = os.path.join(output_dir, channel_name)
            os.makedirs(channel_img_folder, exist_ok=True)
            decoded_frames = HevcDecoder.decode_frames_v2(
                hevc_bytes_io=hevc
            )
            self.del_channel_hevc(channel_name, hevc)
            for frame, timestamp in zip(decoded_frames, timestamps):
                save_path = os.path.join(channel_img_folder, f"{timestamp}.jpeg")
                frame.to_image().save(save_path)
                self.local_channel_file_path[channel_name].append(save_path)


    def process_channel(self, timestamps:list, hevc:bytes, channel_name:str, width:int, height:int):
        channel_img_folder = self.get_topic_file_folder(channel=channel_name)
        decoded_frames = HevcDecoder.decode_frames_v2(
            hevc_bytes_io=hevc
        )
        self.del_channel_hevc(channel_name, hevc)
        for frame, timestamp in zip(decoded_frames, timestamps):
            save_path = os.path.join(channel_img_folder, f"{timestamp}.jpeg")
            frame.to_image().save(save_path)
            self.local_channel_file_path[channel_name].append(save_path)

    def del_channel_hevc(self, channel_name, hevc):
        del hevc
        del self.channel_bytes[channel_name]
        logging.info(f"Success del channel byte, channel: {channel_name}")

    def regen_topic_from_cam_channel(self, channel):
        return "/" + channel.split("CAM")[0].replace("_", "/") + "CAM" + channel.split("CAM")[1]
    
    def expand_cam_name_as_channel(self, cam_name):
        return f"qcraft_logger_camera_{cam_name}"

    def gen_img_uuid(self):
        ...

    def generate_image_raw(self, topic_id, timestamp, file_oss_path_key):
        image_raw_json = {
            "id": self.gen_image_uuid(topic_id=topic_id, timestamp=timestamp),
            "collect_date": self.collect_date,
            "prefix": file_oss_path_key,
            "timestamp": timestamp,
            "topic_id": topic_id
        }
        return image_raw_json

    def upload_image_raw_json(self, image_raw_jsons, channel):
        bag_oss_upload_key = self.get_parsed_bag_oss_upload_key()
        local_parsed_path = self.get_parsed_local_folder()
        local_image_raw_file = f"{local_parsed_path}/{channel}.json"
        with open(local_image_raw_file, 'w') as file:
            json.dump(image_raw_jsons, file)
        # with_output(f"rclone copy {local_image_raw_file} oss:{self.parsed_bucket}/{bag_oss_upload_key}")
        logging.info(f"Success upload image raw json: {local_image_raw_file} to oss")


    def batch_upload(self) -> list:
        poc_list = []
        for channel, local_path_list in self.local_channel_file_path.items():
            if len(self.channel_timestamp[channel]) == 0:
                continue
            topic = self.regen_topic_from_cam_channel(channel)  
            topic_id = self.gen_topic_uuid(topic_name=topic)
            oss_folder_key = self.get_channel_oss_upload_key(channel)

            image_raw_jsons = []
            for file_ in local_path_list:
                file_oss_path_key = os.path.join(oss_folder_key, os.path.basename(file_))
                timestamp = int(os.path.basename(file_).split(".")[0])
                image_raw_json = self.generate_image_raw(topic_id, timestamp, file_oss_path_key)
                self.uploaded.append(image_raw_json)
                image_raw_jsons.append(image_raw_json)
            self.upload_image_raw_json(image_raw_jsons, channel)

            self.uploaded_topic.append({
                    "id": topic_id,
                    "topic_name": topic,
                    "start_time": min(self.channel_timestamp[channel]),
                    "end_time": max(self.channel_timestamp[channel]),
                    "collect_date": self.collect_date,
                    "frame_num": len(self.channel_timestamp[channel]),
                    "car_id": self.vehicle_name,
                    "vin": self.vehicle_name,
                    "prefix": self.get_topic_oss_upload_key(topic)
                }
            )
            self.upload_with_rclone(channel)
            logging.info(f"Success upload topic: {channel}, frame num: {len(local_path_list)}")
        return poc_list

    def run(self):
        thread_l = []
        logging.info(f"Start image handler run")
        for channel, meta in self.channel_meta.items():
            t = Thread(
                target=self.process_channel,
                args=(
                    self.channel_timestamp[channel],
                    self.channel_bytes[channel],
                    channel,
                    meta["width"],
                    meta["height"]
                )
            )
            t.start()
            thread_l.append(t)

        for th in thread_l:
            th.join()

        
        pocs = self.batch_upload()
        for poc, channel, frame_num in pocs:
            poc.wait()
            logging.info(f"Success upload topic: {channel}, frame num: {frame_num}")

    # 重新初始化topic_data
    def reset(self):
        self.channel_bytes = {}
        self.channel_meta = {}
        self.uploaded = []
        self.uploaded_topic = []
        self.channel_timestamp = defaultdict(list)
        self.local_channel_file_path = defaultdict(list)
