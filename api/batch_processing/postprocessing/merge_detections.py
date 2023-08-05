########
#
# merge_detections.py
#
# Merge high-confidence detections from one results file into another file,
# when the target file does not detect anything on an image.
#
# If you want to literally merge two .json files, see combine_api_outputs.py.
#
########

#%% Constants and imports

import argparse
import sys
import json
import os
from tqdm import tqdm

from ct_utils import get_iou

#%% Structs

class MergeDetectionsOptions:
    
    def __init__(self):
        
        self.max_detection_size = 1.01
        self.min_detection_size = 0
        self.source_confidence_thresholds = [0.8]
        
        # Don't bother merging into target images where the max detection is already
        # higher than this threshold
        self.target_confidence_threshold = 0.8
        
        # If you want to merge only certain categories, specify one
        # (but not both) of these.
        self.categories_to_include = None
        self.categories_to_exclude = None
        
        self.merge_empty_only = False
        self.iou_threshold = 0.9

#%% Main function

def merge_detections(source_files,target_file,output_file,options=None):
    
    if isinstance(source_files,str):
        source_files = [source_files]    
        
    if options is None:
        options = MergeDetectionsOptions()    
        
    assert not ((options.categories_to_exclude is not None) and \
                (options.categories_to_include is not None)), \
                'categories_to_include and categories_to_exclude are mutually exclusive'
    
    if options.categories_to_exclude is not None:
        options.categories_to_exclude = [int(c) for c in options.categories_to_exclude]
        
    if options.categories_to_include is not None:
        options.categories_to_include = [int(c) for c in options.categories_to_include]
        
    assert len(source_files) == len(options.source_confidence_thresholds)
    
    for fn in source_files:
        assert os.path.isfile(fn), 'Could not find source file {}'.format(fn)
    
    assert os.path.isfile(target_file)
    
    os.makedirs(os.path.dirname(output_file),exist_ok=True)
    
    with open(target_file,'r') as f:
        output_data = json.load(f)

    print('Loaded results for {} images'.format(len(output_data['images'])))
    
    fn_to_image = {}
    
    # im = output_data['images'][0]
    for im in output_data['images']:
        fn_to_image[im['file']] = im
    
    if 'detections_transferred_from' not in output_data['info']:
        output_data['info']['detections_transferred_from'] = []

    if 'detector' not in output_data['info']:
        output_data['info']['detector'] = 'MDv4 (assumed)'
        
    detection_categories_raw = output_data['detection_categories'].keys()
    
    # Determine whether we should be processing all categories, or just a subset
    # of categories.
    detection_categories = []

    if options.categories_to_exclude is not None:    
        for c in detection_categories_raw:
            if int(c) not in options.categories_to_exclude:
                detection_categories.append(c)
            else:
                print('Excluding category {}'.format(c))
    elif options.categories_to_include is not None:
        for c in detection_categories_raw:
            if int(c) in options.categories_to_include:
                print('Including category {}'.format(c))
                detection_categories.append(c)
    else:
        detection_categories = detection_categories_raw
    
    # i_source_file = 0; source_file = source_files[i_source_file]
    for i_source_file,source_file in enumerate(source_files):
        
        print('Processing detections from file {}'.format(source_file))
        
        with open(source_file,'r') as f:
            source_data = json.load(f)
        
        if 'detector' in source_data['info']:
            source_detector_name = source_data['info']['detector']
        else:
            source_detector_name = os.path.basename(source_file)
        
        output_data['info']['detections_transferred_from'].append(os.path.basename(source_file))
        output_data['info']['detector'] = output_data['info']['detector'] + ' + ' + source_detector_name
        
        assert source_data['detection_categories'] == output_data['detection_categories']
        
        source_confidence_threshold = options.source_confidence_thresholds[i_source_file]
        
        # source_im = source_data['images'][0]
        for source_im in tqdm(source_data['images']):
            
            image_filename = source_im['file']            
            
            assert image_filename in fn_to_image, 'Image {} not in target image set'.format(image_filename)
            target_im = fn_to_image[image_filename]
            
            if 'detections' not in source_im or source_im['detections'] is None:
                continue
            
            if 'detections' not in target_im or target_im['detections'] is None:
                continue
                    
            source_detections_this_image = source_im['detections']
            target_detections_this_image = target_im['detections']
              
            detections_to_transfer = []
            
            # detection_category = list(detection_categories)[0]
            for detection_category in detection_categories:
                
                target_detections_this_category = \
                    [det for det in target_detections_this_image if det['category'] == \
                     detection_category]
                
                max_target_confidence_this_category = 0.0
                
                if len(target_detections_this_category) > 0:
                    max_target_confidence_this_category = max([det['conf'] for \
                      det in target_detections_this_category])
                
                # This is already a detection, no need to proceed looking for detections to 
                # transfer
                if options.merge_empty_only and max_target_confidence_this_category >= options.target_confidence_threshold:
                    continue
                
                source_detections_this_category_raw = [det for det in \
                  source_detections_this_image if det['category'] == detection_category]
                
                # Boxes are x/y/w/h
                # source_sizes = [det['bbox'][2]*det['bbox'][3] for det in source_detections_this_category_raw]
                
                # Only look at boxes below the size threshold
                source_detections_this_category_filtered = [
                    det for det in source_detections_this_category_raw if \
                        (det['bbox'][2]*det['bbox'][3] <= options.max_detection_size) and \
                        (det['bbox'][2]*det['bbox'][3] >= options.min_detection_size) \
                        ]
                                
                for det in source_detections_this_category_filtered:
                    if det['conf'] >= source_confidence_threshold:

                        #check only whole images
                        if options.merge_empty_only:
                            det['transferred_from'] = source_detector_name
                            detections_to_transfer.append(det)

                        #check individual detecions                       
                        else:         
                            nomatch = True
                            for target_detection in target_detections_this_category:
                                if get_iou(det['bbox'],target_detection['bbox']) >= options.iou_threshold:
                                    nomatch = False
                                    break
                            if nomatch:
                                det['transferred_from'] = source_detector_name
                                detections_to_transfer.append(det)

                # ...for each detection within category                            
                                    
            # ...for each detection category
            
            if len(detections_to_transfer) > 0:
                # print('Adding {} detections to image {}'.format(len(detections_to_transfer),image_filename))
                detections = fn_to_image[image_filename]['detections']                
                detections.extend(detections_to_transfer)

                # Update the max_detection_conf field (if present)
                if 'max_detection_conf' in fn_to_image[image_filename]:
                    fn_to_image[image_filename]['max_detection_conf'] = \
                        max([d['conf'] for d in detections])
                
        # ...for each image
        
    # ...for each source file        
    
    with open(output_file,'w') as f:
        json.dump(output_data,f,indent=2)
    
    print('Saved merged results to {}'.format(output_file))

def main():
    parser = argparse.ArgumentParser(
        description='Module to merge detections from one or more source files into an existing target file')
    parser.add_argument(
        'source_files',
        nargs="+",
        help='Path to source file(s) to merge from')
    parser.add_argument(
        'target_file',
        help='Path to a single file to merge detections from source into')
    parser.add_argument(
        'output_file',
        help='Path to output JSON results file, should end with a .json extension')
    parser.add_argument(
        '--max_detection_size',
        type=float,
        default=1.01,
        help='Ignore detections with an area larger than this')    
    parser.add_argument(
        '--min_detection_size',
        default=0,
        type=float,
        help='Ignore detections with an area smaller than this')
    parser.add_argument(
        '--source_confidence_thresholds',
        nargs="+",
        type=float,
        default=[0.8],
        help='List of thresholds for each source file. ' + \
            'Merge only if the source file\'s detection confidence is higher than it\'s corresponding threshold here.')
    parser.add_argument(
        '--target_confidence_threshold',
        type=float,
        default=0.8,
        help='Don\'t merge if target file\'s detection confidence is already higher than this')
    parser.add_argument(
        '--categories_to_include',
        type=int,
        nargs="+",
        default=None,
        help='List of detection categories to include where 1=animal, 2=person, 3=vehicle.' + \
              'For example --categories_to_include=1,2 would include animals and people')
    parser.add_argument(
        '--categories_to_exclude',
        type=int,
        nargs="+",
        default=None,
        help='List of detection categories to include where 1=animal, 2=person, 3=vehicle.' + \
              'For example --categories_to_exclude=2,3 would exclude people and vehicles')
    parser.add_argument(
        '--merge_empty_only',
        action='store_true',
        help='Ignore individual detections and only merge images for which the target file contains no detections')   
    parser.add_argument(
        '--iou_threshold',
        type=float,
        default=0.9,
        help='Sets the minimum IoU for a source detection to be considered the same as the target detection')          

    if len(sys.argv[1:]) == 0:
        parser.print_help()
        parser.exit()

    args = parser.parse_args()

    options = MergeDetectionsOptions()
    options.max_detection_size=args.max_detection_size
    options.min_detection_size=args.min_detection_size
    options.source_confidence_thresholds=args.source_confidence_thresholds
    options.target_confidence_threshold=args.target_confidence_threshold
    options.categories_to_include=args.categories_to_include
    options.categories_to_exclude=args.categories_to_exclude
    options.merge_empty_only=args.merge_empty_only
    options.iou_threshold=args.iou_threshold

    merge_detections(args.source_files, args.target_file, args.output_file, options)

#%% Test driver

if False:
    
    #%%
    
    options = MergeDetectionsOptions()
    options.max_detection_size = 0.1
    options.target_confidence_threshold = 0.3
    options.categories_to_include = [1]
    source_files = ['/home/user/postprocessing/iwildcam/iwildcam-mdv4-2022-05-01/combined_api_outputs/iwildcam-mdv4-2022-05-01_detections.json']
    options.source_confidence_thresholds = [0.8]
    target_file = '/home/user/postprocessing/iwildcam/iwildcam-mdv5-camcocoinat-2022-05-02/combined_api_outputs/iwildcam-mdv5-camcocoinat-2022-05-02_detections.json'
    output_file = '/home/user/postprocessing/iwildcam/merged-detections/mdv4_mdv5-camcocoinat-2022-05-02.json'
    merge_detections(source_files, target_file, output_file, options)
    
    options = MergeDetectionsOptions()
    options.max_detection_size = 0.1
    options.target_confidence_threshold = 0.3
    options.categories_to_include = [1]
    source_files = [
        '/home/user/postprocessing/iwildcam/iwildcam-mdv4-2022-05-01/combined_api_outputs/iwildcam-mdv4-2022-05-01_detections.json',
        '/home/user/postprocessing/iwildcam/iwildcam-mdv5-camonly-2022-05-02/combined_api_outputs/iwildcam-mdv5-camonly-2022-05-02_detections.json',
        ]
    options.source_confidence_thresholds = [0.8,0.5]
    target_file = '/home/user/postprocessing/iwildcam/iwildcam-mdv5-camcocoinat-2022-05-02/combined_api_outputs/iwildcam-mdv5-camcocoinat-2022-05-02_detections.json'
    output_file = '/home/user/postprocessing/iwildcam/merged-detections/mdv4_mdv5-camonly_mdv5-camcocoinat-2022-05-02.json'
    merge_detections(source_files, target_file, output_file, options)
    
if __name__ == '__main__':
    main()

