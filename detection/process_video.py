########
#
# process_video.py
#
# Split a video (or folder of videos) into frames, run the frames through run_detector_batch.py,
# and optionally stitch together results into a new video with detection boxes.
#
########

#%% Imports

import os
import sys
import tempfile
import argparse
import itertools
import json
import shutil
import getpass

from detection import run_detector_batch
from md_visualization import visualize_detector_output
from md_utils.ct_utils import args_to_object
from detection.video_utils import video_to_frames
from detection.video_utils import frames_to_video
from detection.video_utils import frame_results_to_video_results
from detection.video_utils import video_folder_to_frames
from uuid import uuid1

from functools import partial
from multiprocessing import Pool

from detection.video_utils import default_fourcc


#%% Options classes

class ProcessVideoOptions:

    # Can be a model filename (.pt or .pb) or a model name (e.g. "MDV5A")
    model_file = 'MDV5A'
    
    # Can be a file or a folder
    input_video_file = ''

    output_json_file = None
    
    # Only relevant if render_output_video is True
    output_video_file = None
    
    # Folder to use for extracted frames
    frame_folder = None
    
    # Folder to use for rendered frames (if rendering output video)
    frame_rendering_folder = None
    
    # Should we render a video with detection boxes?
    #
    # Only supported when processing a single video, not a folder.
    render_output_video = False
    
    # If we are rendering boxes to a new video, should we keep the temporary
    # rendered frames?
    keep_rendered_frames = False
    
    # Should we keep the extracted frames?
    keep_extracted_frames = False
    
    # Should we delete the entire folder the extracted frames are written to?
    #
    # By default, we delete the frame files but leave the (probably-empty) folder in place.
    force_extracted_frame_folder_deletion = False
    
    # Should we delete the entire folder the rendered frames are written to?
    #
    # By default, we delete the frame files but leave the (probably-empty) folder in place.
    force_rendered_frame_folder_deletion = False
        
    reuse_results_if_available = False
    reuse_frames_if_available = False
    
    recursive = False 
    verbose = False
    
    fourcc = None

    rendering_confidence_threshold = None
    json_confidence_threshold = 0.005
    frame_sample = None
    
    n_cores = 1

    debug_max_frames = -1
    
    class_mapping_filename = None


#%% Functions

def process_video(options):
    """
    Process a single video
    """

    if options.output_json_file is None:
        options.output_json_file = options.input_video_file + '.json'

    if options.render_output_video and (options.output_video_file is None):
        options.output_video_file = options.input_video_file + '.detections.mp4'

    tempdir = os.path.join(tempfile.gettempdir(), 'process_camera_trap_video')    
    os.makedirs(tempdir,exist_ok=True)
    
    # Generate unique subdirectory name for current user
    user_subdirectory = f"{getpass.getuser()}_{str(uuid1())}"
    user_tempdir = os.path.join(tempdir, user_subdirectory)
    os.makedirs(user_tempdir, exist_ok=True)

    
    # Initialize flag to track if frame_ouput_folder was created by the script
    frame_output_folder_created = False
    
    if options.frame_folder is not None:
        frame_output_folder = options.frame_folder
    else:
        frame_output_folder = os.path.join(
            user_tempdir, os.path.basename(options.input_video_file) + '_frames_' + str(uuid1()))
        frame_output_folder_created = True
   
    os.makedirs(frame_output_folder, exist_ok=True)

    frame_filenames, Fs = video_to_frames(
        options.input_video_file, frame_output_folder, 
        every_n_frames=options.frame_sample, overwrite=(not options.reuse_frames_if_available))

    image_file_names = frame_filenames
    if options.debug_max_frames > 0:
        image_file_names = image_file_names[0:options.debug_max_frames]

    if options.reuse_results_if_available and \
        os.path.isfile(options.output_json_file):
            print('Loading results from {}'.format(options.output_json_file))
            with open(options.output_json_file,'r') as f:
                results = json.load(f)
    else:
        results = run_detector_batch.load_and_run_detector_batch(
            options.model_file, image_file_names,
            confidence_threshold=options.json_confidence_threshold,
            n_cores=options.n_cores,
            quiet=(not options.verbose),
            class_mapping_filename=options.class_mapping_filename)
    
        run_detector_batch.write_results_to_file(
            results, options.output_json_file,
            relative_path_base=frame_output_folder,
            detector_file=options.model_file,
            custom_metadata={'video_frame_rate':Fs})

    
    ## (Optionally) render output video
    
    if options.render_output_video:
        
        # Initialize flag to track if frame_rendering_folder was created by the scipt
        frame_rendering_folder_created = False

        # Render detections to images
        if options.frame_rendering_folder is not None:
            rendering_output_dir = options.frame_rendering_folder
        else:
            rendering_output_dir = os.path.join(
                tempdir, os.path.basename(options.input_video_file) + '_detections')
            frame_rendering_folder_created = True
            
        os.makedirs(rendering_output_dir,exist_ok=True)
        
        detected_frame_files = visualize_detector_output.visualize_detector_output(
            detector_output_path=options.output_json_file,
            out_dir=rendering_output_dir,
            images_dir=frame_output_folder,
            confidence_threshold=options.rendering_confidence_threshold)

        # Combine into a video
        if options.frame_sample is None:
            rendering_fs = Fs
        else:
            rendering_fs = Fs / options.frame_sample
            
        print('Rendering video to {} at {} fps (original video {} fps)'.format(
            options.output_video_file,rendering_fs,Fs))
        frames_to_video(detected_frame_files, rendering_fs, options.output_video_file, codec_spec=options.fourcc)
        
        # Delete the temporary directory we used for detection images
        if not options.keep_rendered_frames:
            try:
                if options.force_rendered_frame_folder_deletion or (frame_rendering_folder_created and options.output_video_file != rendering_output_dir):
                    shutil.rmtree(rendering_output_dir)
                else:
                    for rendered_frame_fn in detected_frame_files:
                        os.remove(rendered_frame_fn)
            except Exception as e:
                print('Warning: error deleting rendered frames from folder {}:\n{}'.format(
                    rendering_output_dir,str(e)))
                pass
    
    # ...if we're rendering video
    
    
    ## (Optionally) delete the extracted frames
    
    if not options.keep_extracted_frames:
        
        try:
            if options.force_extracted_frame_folder_deletion or (frame_output_folder_created and options.ouput_json_file != frame_output_folder):
                print('Recursively deleting frame output folder {}'.format(frame_output_folder))
                shutil.rmtree(frame_output_folder)
            else:
                for extracted_frame_fn in frame_filenames:
                    os.remove(extracted_frame_fn)
        except Exception as e:
            print('Warning: error removing extracted frames from folder {}:\n{}'.format(
                frame_output_folder,str(e)))
            pass
        
    return results

# ...process_video()


def process_video_folder(options):
    """
    Process a folder of videos    
    """
    
    ## Validate options

    assert os.path.isdir(options.input_video_file), \
        '{} is not a folder'.format(options.input_video_file)
           
    assert options.output_json_file is not None, \
        'When processing a folder, you must specify an output .json file'
                         
    assert options.output_json_file.endswith('.json')
    video_json = options.output_json_file
    frames_json = options.output_json_file.replace('.json','.frames.json')
    os.makedirs(os.path.dirname(video_json),exist_ok=True)
    
    
    ## Split every video into frames
    
    if options.frame_folder is not None:
        frame_output_folder = options.frame_folder
    else:
        tempdir = os.path.join(tempfile.gettempdir(), 'process_camera_trap_video')
        os.makedirs(tempdir,exist_ok=True)
        
        # Generate unique subdirectory name for current user
        user_subdirectory = f"{getpass.getuser()}_{str(uuid1())}"
        user_tempdir = os.path.join(tempdir, user_subdirectory)
        os.makedirs(user_tempdir, exist_ok=True)

        frame_output_folder = os.path.join(
            tempdir, os.path.basename(options.input_video_file) + '_frames_' + str(uuid1()))

    os.makedirs(frame_output_folder, exist_ok=True)

    print('Extracting frames')
    frame_filenames, Fs, video_filenames = \
        video_folder_to_frames(input_folder=options.input_video_file,
                               output_folder_base=frame_output_folder, 
                               recursive=options.recursive, 
                               overwrite=(not options.reuse_frames_if_available),
                               n_threads=options.n_cores,every_n_frames=options.frame_sample,
                               verbose=options.verbose)
    
    image_file_names = list(itertools.chain.from_iterable(frame_filenames))
    
    if len(image_file_names) == 0:
        if len(video_filenames) == 0:
            print('No videos found in folder {}'.format(options.input_video_file))
        else:
            print('No frames extracted from folder {}, this may be due to an '\
                  'unsupported video codec'.format(options.input_video_file))
        return

    if options.debug_max_frames is not None and options.debug_max_frames > 0:
        image_file_names = image_file_names[0:options.debug_max_frames]
    
    
    ## Run MegaDetector on the extracted frames
    
    if options.reuse_results_if_available and \
        os.path.isfile(frames_json):
            print('Loading results from {}'.format(frames_json))
            results = None
    else:
        print('Running MegaDetector')
        results = run_detector_batch.load_and_run_detector_batch(
            options.model_file, image_file_names,
            confidence_threshold=options.json_confidence_threshold,
            n_cores=options.n_cores,
            quiet=(not options.verbose),
            class_mapping_filename=options.class_mapping_filename)
    
        run_detector_batch.write_results_to_file(
            results, frames_json,
            relative_path_base=frame_output_folder,
            detector_file=options.model_file,
            custom_metadata={'video_frame_rate':Fs})
    
    
    ## Convert frame-level results to video-level results

    print('Converting frame-level results to video-level results')
    frame_results_to_video_results(frames_json,video_json)


    ## (Optionally) render output videos
    
    if options.render_output_video:
        
        # Initialize flag to track if frame_rendering_folder was created by the script
        frame_rendering_folder_created = False

        # Render detections to images
        if options.frame_rendering_folder is not None:
            frame_rendering_output_dir = options.frame_rendering_folder
        else:
            frame_rendering_output_dir = os.path.join(
                tempdir, os.path.basename(options.input_video_file) + '_detections')
            frame_rendering_folder_created = True
        
        os.makedirs(frame_rendering_output_dir,exist_ok=True)
        
        detected_frame_files = visualize_detector_output.visualize_detector_output(
            detector_output_path=frames_json,
            out_dir=frame_rendering_output_dir,
            images_dir=frame_output_folder,
            confidence_threshold=options.rendering_confidence_threshold,
            preserve_path_structure=True,
            output_image_width=-1)
        
        # Choose an output folder
        output_folder_is_input_folder = False
        if options.output_video_file is not None:
            if os.path.isfile(options.output_video_file):
                raise ValueError('Rendering videos for a folder, but an existing file was specified as output')
            elif options.output_video_file == options.input_video_file:
                output_folder_is_input_folder = True
                output_video_folder = options.input_video_file
            else:
                os.makedirs(options.output_video_file,exist_ok=True)
                output_video_folder = options.output_video_file
        else:
            output_folder_is_input_folder = True
            output_video_folder = options.input_video_file
                                
       
        
def process_single_video(video_file, options, frame_rendering_output_dir, detected_frame_files, output_folder_is_input_folder, output_video_folder, Fs, frame_sample):
    video_fs = Fs[video_filenames.index(video_file)]
    if frame_sample is None:
        rendering_fs = video_fs
    else:
        rendering_fs = video_fs / frame_sample
        
    input_video_file_relative = os.path.relpath(video_file, options.input_video_file)
    video_frame_output_folder = os.path.join(frame_rendering_output_dir, input_video_file_relative)
    assert os.path.isdir(video_frame_output_folder), \
        'Could not find frame folder for video {}'.format(input_video_file_relative)
        
    # Find the corresponding rendered frame folder
    video_frame_files = [fn for fn in detected_frame_files if \
                                 fn.startswith(video_frame_output_folder)]
    assert len(video_frame_files) > 0, 'Could not find rendered frames for video {}'.format(
                input_video_file_relative)
            
    from md_utils.path_utils import insert_before_extension
            
    # Select the output filename for the rendered video
    if output_folder_is_input_folder:
        video_output_file = insert_before_extension(input_video_file_abs,'annotated','_')
    else:
        video_output_file = os.path.join(output_video_folder,input_video_file_relative)
            
    os.makedirs(os.path.dirname(video_output_file),exist_ok=True)
            
    # Create the output video            
    print('Rendering detections for video {} to {} at {} fps (original video {} fps)'.format(
        input_video_file_relative,video_output_file,rendering_fs,video_fs))
    frames_to_video(video_frame_files, rendering_fs, video_output_file, codec_spec=options.fourcc)

# Parallelize this loop
pool = Pool(processes = options.n_cores)
pool.map(partial(process_single_video, options=options, frame_rendering_output_dir=frame_rendering_output_dir,
                 detected_frame_files=detected_frame_files, output_folder_is_input_folder=output_folder_is_input_folder,
                 output_video_folder=output_video_folder, Fs=Fs, frame_sample=options.frame_sample), video_filenames)

pool.close()
pool.join()        
   
# ...for each video
        
# Possibly clean up rendered frames
if not options.keep_rendered_frames:
    try:
        if options.force_rendered_frame_folder_deletion or (frame_rendering_folder_created and frame_rendering_output_dir != video_output_file):
                    shutil.rmtree(frame_rendering_output_dir)
        else:
            for rendered_frame_fn in detected_frame_files:
                os.remove(rendered_frame_fn)
    except Exception as e:
        print('Warning: error deleting rendered frames from folder {}:\n{}'.format(
            frame_rendering_output_dir,str(e)))
        pass
        
    # ...if we're rendering video
    
    
    ## (Optionally) delete the extracted frames
    
if not options.keep_extracted_frames:
    try:
        print('Deleting frame cache')
        if options.force_extracted_frame_folder_deletion:
                print('Recursively deleting frame output folder {}'.format(frame_output_folder))
                shutil.rmtree(frame_output_folder)
        else:
            for frame_fn in image_file_names:
                os.remove(frame_fn)
    except Exception as e:
        print('Warning: error deleting frames from folder {}:\n{}'.format(
            frame_output_folder,str(e)))
        pass
        
    
# ...process_video_folder()


def options_to_command(options):
    
    cmd = 'python process_video.py'
    cmd += ' "' + options.model_file + '"'
    cmd += ' "' + options.input_video_file + '"'
    
    if options.recursive:
        cmd += ' --recursive'
    if options.frame_folder is not None:
        cmd += ' --frame_folder' + ' "' + options.frame_folder + '"'
    if options.frame_rendering_folder is not None:
        cmd += ' --frame_rendering_folder' + ' "' + options.frame_rendering_folder + '"'    
    if options.output_json_file is not None:
        cmd += ' --output_json_file' + ' "' + options.output_json_file + '"'
    if options.output_video_file is not None:
        cmd += ' --output_video_file' + ' "' + options.output_video_file + '"'
    if options.keep_extracted_frames:
        cmd += ' --keep_extracted_frames'
    if options.reuse_results_if_available:
        cmd += ' --reuse_results_if_available'    
    if options.reuse_frames_if_available:
        cmd += ' --reuse_frames_if_available'
    if options.render_output_video:
        cmd += ' --render_output_video'
    if options.keep_rendered_frames:
        cmd += ' --keep_rendered_frames'    
    if options.rendering_confidence_threshold is not None:
        cmd += ' --rendering_confidence_threshold ' + str(options.rendering_confidence_threshold)
    if options.json_confidence_threshold is not None:
        cmd += ' --json_confidence_threshold ' + str(options.json_confidence_threshold)
    if options.n_cores is not None:
        cmd += ' --n_cores ' + str(options.n_cores)
    if options.frame_sample is not None:
        cmd += ' --frame_sample ' + str(options.frame_sample)
    if options.debug_max_frames is not None:
        cmd += ' --debug_max_frames ' + str(options.debug_max_frames)
    if options.class_mapping_filename is not None:
        cmd += ' --class_mapping_filename ' + str(options.class_mapping_filename)
    if options.fourcc is not None:
        cmd += ' --fourcc ' + options.fourcc

    return cmd

    
#%% Interactive driver

if False:    
        
    #%% Process a folder of videos
    
    model_file = 'MDV5A'
    input_dir = r'c:\git\MegaDetector\test_images\test_images'    
    frame_folder = r'g:\temp\video_test\frames'
    rendering_folder = r'g:\temp\video_test\rendered-frames'
    output_json_file = r'g:\temp\video_test\video-test.json'
    output_video_folder = r'g:\temp\video_test\output_videos'    
    
    print('Processing folder {}'.format(input_dir))
    
    options = ProcessVideoOptions()    
    options.model_file = model_file
    options.input_video_file = input_dir
    options.output_video_file = output_video_folder
    options.frame_folder = frame_folder
    options.output_json_file = output_json_file
    options.frame_rendering_folder = rendering_folder
    options.render_output_video = True
    options.keep_extracted_frames = True
    options.keep_rendered_frames = True
    options.recursive = True
    options.reuse_frames_if_available = True
    options.reuse_results_if_available = True
    # options.confidence_threshold = 0.15
    # options.fourcc = 'mp4v'        
    
    cmd = options_to_command(options)
    print(cmd)
    # import clipboard; clipboard.copy(cmd)
    
    if False:
        process_video_folder(options)
        
    
    #%% Process a single video

    fn = os.path.expanduser('~/tmp/video-test/test-video.mp4')
    model_file = 'MDV5A'
    input_video_file = fn
    frame_folder = os.path.expanduser('~/tmp/video-test/frames')
    rendering_folder = os.path.expanduser('~/tmp/video-test/rendered-frames')
    
    options = ProcessVideoOptions()
    options.model_file = model_file
    options.input_video_file = input_video_file
    options.frame_folder = frame_folder
    options.frame_rendering_folder = rendering_folder
    options.render_output_video = True
    options.output_video_file = os.path.expanduser('~/tmp/video-test/detections.mp4')
    
    cmd = options_to_command(options)
    print(cmd)
    # import clipboard; clipboard.copy(cmd)
    
    if False:        
        process_video(options)    
            
    
#%% Command-line driver

def main():

    default_options = ProcessVideoOptions()
    
    parser = argparse.ArgumentParser(description=(
        'Run MegaDetector on each frame in a video (or every Nth frame), optionally '\
        'producing a new video with detections annotated'))

    parser.add_argument('model_file', type=str,
                        help='MegaDetector model file (.pt or .pb) or model name (e.g. "MDV5A")')

    parser.add_argument('input_video_file', type=str,
                        help='video file (or folder) to process')

    parser.add_argument('--recursive', action='store_true',
                        help='recurse into [input_video_file]; only meaningful if a folder '\
                         'is specified as input')
    
    parser.add_argument('--frame_folder', type=str, default=None,
                        help='folder to use for intermediate frame storage, defaults to a folder '\
                        'in the system temporary folder')
        
    parser.add_argument('--frame_rendering_folder', type=str, default=None,
                        help='folder to use for rendered frame storage, defaults to a folder in '\
                        'the system temporary folder')
    
    parser.add_argument('--output_json_file', type=str,
                        default=None, help='.json output file, defaults to [video file].json')

    parser.add_argument('--output_video_file', type=str,
                        default=None, help='video output file (or folder), defaults to '\
                            '[video file].mp4 for files, or [video file]_annotated for folders')

    parser.add_argument('--keep_extracted_frames',
                       action='store_true', help='Disable the deletion of extracted frames')
    
    parser.add_argument('--reuse_frames_if_available',
                       action='store_true', help="Don't extract frames that are already available in the frame extraction folder")
    
    parser.add_argument('--reuse_results_if_available',
                       action='store_true', help='If the output .json files exists, and this flag is set,'\
                           'we\'ll skip running MegaDetector')
    
    parser.add_argument('--render_output_video', action='store_true',
                        help='enable video output rendering (not rendered by default)')

    parser.add_argument('--fourcc', default=default_fourcc,
                        help='fourcc code to use for video encoding (default {}), only used if render_output_video is True'.format(default_fourcc))
    
    parser.add_argument('--keep_rendered_frames',
                       action='store_true', help='Disable the deletion of rendered (w/boxes) frames')

    parser.add_argument('--force_extracted_frame_folder_deletion',
                       action='store_true', help='By default, when keep_extracted_frames is False, we '\
                           'delete the frames, but leave the (probably-empty) folder in place.  This option '\
                           'forces deletion of the folder as well.  Use at your own risk; does not check '\
                           'whether other files were present in the folder.')
        
    parser.add_argument('--force_rendered_frame_folder_deletion',
                       action='store_true', help='By default, when keep_rendered_frames is False, we '\
                           'delete the frames, but leave the (probably-empty) folder in place.  This option '\
                           'forces deletion of the folder as well.  Use at your own risk; does not check '\
                           'whether other files were present in the folder.')
        
    parser.add_argument('--rendering_confidence_threshold', type=float,
                        default=None, help="don't render boxes with confidence below this threshold (defaults to choosing based on the MD version)")

    parser.add_argument('--json_confidence_threshold', type=float,
                        default=0.0, help="don't include boxes in the .json file with confidence "\
                            'below this threshold (default {})'.format(
                                default_options.json_confidence_threshold))

    parser.add_argument('--n_cores', type=int,
                        default=1, help='number of cores to use for frame separation and detection. '\
                            'If using a GPU, this option will be respected for frame separation but '\
                            'ignored for detection.  Only relevant to frame separation when processing '\
                            'a folder.')

    parser.add_argument('--frame_sample', type=int,
                        default=None, help='process every Nth frame (defaults to every frame)')

    parser.add_argument('--debug_max_frames', type=int,
                        default=-1, help='trim to N frames for debugging (impacts model execution, '\
                            'not frame rendering)')
    
    parser.add_argument('--class_mapping_filename',
                        type=str,
                        default=None, help='Use a non-default class mapping, supplied in a .json file '\
                            'with a dictionary mapping int-strings to strings.  This will also disable '\
                            'the addition of "1" to all category IDs, so your class mapping should start '\
                            'at zero.')

    if len(sys.argv[1:]) == 0:
        parser.print_help()
        parser.exit()
        
    args = parser.parse_args()
    options = ProcessVideoOptions()
    args_to_object(args,options)

    if os.path.isdir(options.input_video_file):
        process_video_folder(options)
    else:
        process_video(options)

if __name__ == '__main__':
    main()
