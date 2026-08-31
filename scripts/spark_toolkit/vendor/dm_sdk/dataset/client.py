from .base import DatasetBasic
from .data_collection import (
    add_data_collection_members,
    create_data_collection,
    delete_data_collection,
    get_data_collection,
    get_data_collection_members,
    remove_data_collection_members,
    update_data_collection,
)
from .dataset import (
    add_dataset_members,
    create_dataset,
    create_new_version,
    find_matching_clips,
    freeze_version,
    get_dataset,
    get_dataset_members,
    get_latest_version,
    get_version,
    load_to_cpfs,
    remove_dataset_members,
    update_dataset,
    update_version,
)


class DatasetClient(DatasetBasic):
    # dataset operations
    create_dataset = create_dataset
    create_new_version = create_new_version
    get_dataset = get_dataset
    get_version = get_version
    get_dataset_members = get_dataset_members
    get_latest_version = get_latest_version
    update_dataset = update_dataset
    update_version = update_version
    remove_dataset_members = remove_dataset_members
    add_dataset_members = add_dataset_members
    load_to_cpfs = load_to_cpfs
    find_matching_clips = find_matching_clips
    freeze_version = freeze_version

    # data collection operations
    create_data_collection = create_data_collection
    get_data_collection = get_data_collection
    update_data_collection = update_data_collection
    delete_data_collection = delete_data_collection
    get_data_collection_members = get_data_collection_members
    add_data_collection_members = add_data_collection_members
    remove_data_collection_members = remove_data_collection_members
