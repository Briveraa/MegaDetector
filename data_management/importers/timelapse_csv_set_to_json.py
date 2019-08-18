###
#
# timelapse_csv_set_to_json.py
#
# Given a directory full of reasonably-consistent Timelapse-exported
# .csvs, assemble a CCT .json.
#
# Assumes that you have a list of all files in the directory tree, including 
# image and .csv files.
#
###

#%% Constants and imports

import uuid
import json
import time
import re
import humanfriendly
import os
import PIL
import pandas as pd
import numpy as np
from tqdm import tqdm

from visualization import visualize_db
import path_utils

# Text file with relative paths to all files (images and .csv files)
input_relative_file_list = r"f:\uw_gardner\all_files_2019.08.17.txt"
output_file = r"f:\uw_gardner\uw_gardner.2019.08.17.json"
preview_base = r"f:\uw_gardner\db_preview"
file_base = 'y:\\'
top_level_image_folder = 'Processed Images'
contributor_name = 'University of Washington'

expected_columns = 'File,RelativePath,Folder,Date,Time,ImageQuality,DeleteFlag,CameraLocation,StartDate,TechnicianName,Empty,Service,Species,HumanActivity,Count,AdultFemale,AdultMale,AdultUnknown,Offspring,YOY,UNK,Collars,Tags,NaturalMarks,Reaction,Illegal,GoodPicture,SecondOpinion,Comments'.\
    split(',')
ignore_fields = 'Unnamed: 29'
required_image_regex = '^Processed'

category_mappings = {'none':'empty'}

check_file_existence = False
retrieve_image_size = False


#%% Read file list, make a list of all image files and all .csv files

with open(input_relative_file_list) as f:
    all_files = f.readlines()
    all_files = [x.strip() for x in all_files] 

image_files = set()
csv_files = []
non_matching_files = []

for fn in all_files:
    
    fnl = fn.lower()
    
    if fnl.endswith('.csv'):
        
        csv_files.append(fn)
        
    elif (fnl.endswith('.jpg') or fnl.endswith('.png')):
        
        if required_image_regex is not None and not re.match(required_image_regex,fn):
            non_matching_files.append(fn)
        else:
            image_files.add(fn)
        
print('Found {} image files and {} .csv files ({} non-matching files)'.format(
        len(image_files),len(csv_files),len(non_matching_files)))

for fn in image_files:
    assert fn.lower().endswith('.jpg')

    
#%% Verify column consistency, create a giant array with all rows from all .csv files

bad_csv_files = []
normalized_dataframes = []

# i_csv = 0; csv_filename = csv_files[0]
for i_csv,csv_filename in enumerate(csv_files):
    
    full_path = os.path.join(file_base,csv_filename)
    try:
        df = pd.read_csv(full_path)        
    except Exception as e:
        if 'invalid start byte' in str(e):
            try:
                print('Read error, reverting to fallback encoding')
                df = pd.read_csv(full_path,encoding='latin1')                
            except Exception as e:
                print('Can''t read file {}: {}'.format(csv_filename,str(e)))
                bad_csv_files.append(csv_filename)
                continue
    
    if not (len(df.columns) == len(expected_columns) and (df.columns == expected_columns).all()):
        extra_fields = ','.join(set(df.columns) - set(expected_columns))
        extra_fields = [x for x in extra_fields if x not in ignore_fields]
        missing_fields = ','.join(set(expected_columns) - set(df.columns))        
        missing_fields = [x for x in missing_fields if x not in ignore_fields]
        if not (len(missing_fields) == 0 and len(extra_fields) == 0):
            print('In file {}, extra fields {}, missing fields {}'.format(csv_filename,
                  extra_fields,missing_fields))
    normalized_df = df[expected_columns].copy()
    normalized_df['source_file'] = csv_filename
    normalized_dataframes.append(normalized_df)
    
print('Ignored {} of {} csv files'.format(len(bad_csv_files),len(csv_files)))
valid_csv_files = [x for x in csv_files if x not in bad_csv_files]

input_metadata = pd.concat(normalized_dataframes)
assert len(input_metadata.columns) == 1 + len(expected_columns)

print('Concatenated all .csv files into a dataframe with {} rows'.format(len(input_metadata)))


#%% Prepare some data structures we'll need for mapping image rows in .csv files to actual image files

# Enumerate all folders containing image files
all_image_folders = set()

for fn in image_files:
    dn = os.path.dirname(fn)
    all_image_folders.add(dn)
    
print('Enumerated {} unique image folders'.format(len(all_image_folders)))    

# In this data set, a site folder looks like:
#
# Processed Images\\OK7658_complete

site_folders = set()
for image_folder in all_image_folders:
    tokens = path_utils.split_path(image_folder)
    site_folders.add(tokens[0] + '/' + tokens[1])


#%% Map .csv files to candidate camera folders
    
csv_filename_to_camera_folder = {}
    
# fn = valid_csv_files[0]
for fn_original in valid_csv_files:
    
    fn = fn_original
    if 'Template' in fn:
        continue
            
    fn = fn.replace('OK7658_OK7658_','OK7658_')
    fn = fn.replace('Moutlrie','Moultrie')
    fn = fn.replace('CameraMoultrie','Moultrie')
    fn = fn.replace('Moultrie','Moultrie_')
    
    csv_filename = os.path.basename(fn)
    pat = '^(?P<site>[^_]+)_(?P<cameranum>[^_]+)_'
    re_result = re.search(pat,csv_filename)
    if re_result is None:
        print('Couldn''t match tokens in {}'.format(csv_filename))
        continue
    site = re_result.group('site')
    
    # Random typos in some filenames
    site = site.replace('NE5735','NE5736')
    site = site.replace('NE3419','NE3149')
    
    cameranum = re_result.group('cameranum')
    
    site_folder = top_level_image_folder + '/' + site
    
    # Some site folders appear as "XXNNNN", some appear as "XXNNNN_complete"
    if site_folder not in site_folders:
        site = site + '_complete'
        site_folder = top_level_image_folder + '/' + site
        if site_folder not in site_folders:    
            print('Could not find site folder for {}'.format(fn))
            continue
        
    camera_folder = top_level_image_folder + '/' + site + '/Camera_' + str(cameranum)
    
    b_found_camera_folder = False
    
    for candidate_camera_folder in all_image_folders:
        
        if candidate_camera_folder.startswith(camera_folder):
            b_found_camera_folder = True
            break
        
    if not b_found_camera_folder:
        print('Could not find camera folder {} for csv {}'.format(camera_folder,fn))
        continue
    
    assert fn not in csv_filename_to_camera_folder
    csv_filename_to_camera_folder[fn_original] = camera_folder

print('Successfully mapped {} of {} csv files to camera folders'.format(len(csv_filename_to_camera_folder),
      len(valid_csv_files)))


for fn in valid_csv_files:
    if 'Template' in fn:
        continue
    if fn not in csv_filename_to_camera_folder:
        print('No camera folder mapping for {}'.format(fn))


#%% Map camera folders to candidate image folders

camera_folders_to_image_folders = {}

for camera_folder in csv_filename_to_camera_folder.values():

    for image_folder in all_image_folders:
        if image_folder.startswith(camera_folder):
            camera_folders_to_image_folders.setdefault(camera_folder,[]).append(image_folder)
    
        
#%% Main loop over labels (prep)

start_time = time.time()

relative_path_to_image = {}
image_id_to_image = {}

images = []
annotations = []
category_name_to_category = {}
files_missing_from_file_list = []
files_missing_on_disk = []

duplicate_image_ids = set()

# Force the empty category to be ID 0
empty_category = {}
empty_category['name'] = 'empty'
empty_category['id'] = 0
category_name_to_category['empty'] = empty_category

next_category_id = 1

ignored_csv_files = set()
ignored_image_folders = set()

# Images that are marked empty and also have a species label
ambiguous_images = []


#%% Main loop over labels (loop)

# i_row = 0; row = input_metadata.iloc[i_row]
for i_row,row in tqdm(input_metadata.iterrows(),total=len(input_metadata)):
# for i_row,row in input_metadata.iterrows():
    
    image_filename = row['File']
    image_folder = row['RelativePath']
    if isinstance(image_folder,float):
        assert np.isnan(image_folder)
        image_folder = row['Folder']
    image_folder = image_folder.replace('\\','/')
    
    # Usually this is just a single folder name, sometimes it's a full path, 
    # which we don't want
    image_folder = path_utils.split_path(image_folder)[-1]
    csv_filename = row['source_file']
        
    if csv_filename not in csv_filename_to_camera_folder:
        if csv_filename not in ignored_csv_files:
            print('No camera folder for {}'.format(csv_filename))
            assert csv_filename in valid_csv_files
            ignored_csv_files.add(csv_filename)
        continue
    
    camera_folder = csv_filename_to_camera_folder[csv_filename]    
    candidate_image_folders = camera_folders_to_image_folders[camera_folder]
    
    image_folder_relative_path = None
    for candidate_image_folder in candidate_image_folders:
        if candidate_image_folder.endswith(image_folder):
            image_folder_relative_path = candidate_image_folder
    if image_folder_relative_path is None:
        camera_image_folder = camera_folder + '_' + image_folder
        if camera_image_folder not in ignored_image_folders:
            print('No image folder for {}'.format(camera_image_folder))            
            ignored_image_folders.add(camera_image_folder)            
            continue

    image_relative_path = image_folder_relative_path + '/' + image_filename
    if image_relative_path not in image_files:
        files_missing_from_file_list.append(image_relative_path)
        continue

    image_id = image_relative_path.replace('_','~').replace('/','_').replace('\\','_')
    
    if image_id in image_id_to_image:

        im = image_id_to_image[image_id]
        assert im['id'] == image_id
        duplicate_image_ids.add(image_id)
            
    else:
        
        im = {}
        im['id'] = image_id
        im['file_name'] = image_relative_path
        im['seq_id'] = '-1'
        images.append(im)
        relative_path_to_image[image_relative_path] = im
        image_id_to_image[image_id] = im
            
        if check_file_existence or retrieve_image_size:
        
            image_full_path = os.path.join(file_base,image_relative_path)        
            
            if check_file_existence:
                if not os.path.isfile(image_full_path):                    
                    files_missing_on_disk.append(image_relative_path)
                
            # Retrieve image width and height
            if retrieve_image_size:
                pil_image = PIL.Image.open(image_full_path)
                width, height = pil_image.size
                im['width'] = width
                im['height'] = height
    
    category_name = row['Species']
    if isinstance(category_name,float):
        assert np.isnan(category_name)
        category_name = None
    else:
        category_name = category_name.lower()
    
    empty_token = row['Empty']
    if empty_token == True:
        if category_name is not None:
            category_name = 'ambiguous'            
            ambiguous_images.append(im)
        else:
            category_name = 'empty'
    else:
        assert empty_token == False
        if category_name is None:
            category_name = 'unlabeled'
                
    if category_name in category_mappings:
        category_name = category_mappings[category_name]
        
    if category_name not in category_name_to_category:
        category = {}
        category['name'] = category_name
        category['id'] = next_category_id
        next_category_id += 1
        category_name_to_category[category_name] = category
    else:
        category = category_name_to_category[category_name]
    
    category_id = category['id']
    
    # Create an annotation
    ann = {}
    
    # The Internet tells me this guarantees uniqueness to a reasonable extent, even
    # beyond the sheer improbability of collisions.
    ann['id'] = str(uuid.uuid1())
    ann['image_id'] = im['id']    
    ann['category_id'] = category_id
    
    annotations.append(ann)
    
# ...for each row in the big table of concatenated .csv files
    
categories = list(category_name_to_category.values())

elapsed = time.time() - start_time
print('Finished verifying file loop in {}, {} images, {} missing images, {} repeat labels, {} ambiguous labels'.format(
        humanfriendly.format_timespan(elapsed), len(images), len(files_missing_from_file_list), 
        len(duplicate_image_ids), len(ambiguous_images)))


#%% Check for un-annnotated images

# Enumerate all images
# list(relative_path_to_image.keys())[0]

unmatched_files = []

for i_image,image_path in enumerate(image_files):
    
    if image_path not in relative_path_to_image:
        unmatched_files.append(image_path)

print('Finished checking {} images to make sure they\'re in the metadata, found {} un-annotated images'.format(
        len(image_files),len(unmatched_files)))


#%% Create info struct

info = {}
info['year'] = 2019
info['version'] = 1
info['description'] = 'COCO style database'
info['secondary_contributor'] = 'Converted to COCO .json by Dan Morris'
info['contributor'] = contributor_name


#%% Write output

json_data = {}
json_data['images'] = images
json_data['annotations'] = annotations
json_data['categories'] = categories
json_data['info'] = info
json.dump(json_data, open(output_file,'w'), indent=1)

print('Finished writing .json file with {} images, {} annotations, and {} categories'.format(
        len(images),len(annotations),len(categories)))


#%% Sanity-check the database's integrity

from data_management.databases import sanity_check_json_db

options = sanity_check_json_db.SanityCheckOptions()
sortedCategories,data = sanity_check_json_db.sanity_check_json_db(output_file, options)


#%% Render a bunch of images to make sure the labels got carried along correctly

options = visualize_db.DbVizOptions()
options.num_to_visualize = 500
options.sort_by_filename = False
options.classes_to_exclude = ['unlabeled','empty']

html_output_file,data = visualize_db.process_images(output_file,preview_base,file_base,options)
# os.startfile(html_output_file)

