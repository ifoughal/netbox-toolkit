from json import load as load_json
from yaml import full_load as yaml_full_load
from yaml import load as yaml_load
from yaml import Loader as yaml_loader

from concurrent.futures import ThreadPoolExecutor
from sys import exc_info
from traceback import format_exc


class ThreadPoolExecutorStackTraced(ThreadPoolExecutor):
    def submit(self, method_to_execute, *args, **kwargs):
        """Submits the wrapped function instead of `method_to_execute`"""
        return super(ThreadPoolExecutorStackTraced, self).submit(
            self._function_wrapper,
            method_to_execute,
            *args, **kwargs
        )

    def _function_wrapper(self, method_to_execute, *args, **kwargs):
        """Wraps `method_to_execute` in order to preserve raised exceptions
        """
        try:
            return method_to_execute(*args, **kwargs)
        except Exception:
            # raise Exception.with_traceback(format_exc())
            raise exc_info()[0](format_exc())


def update_file(data, filename: str, overwrite=True, debug=False, chmod_=0o0660):
    """ Update the content of a txt file with the sent data.
        If the file doesn't exist, it will be created.
    Args:
        data (str): The data that will update the content of the file.
        file (str): The name of the file.
        overwrite (bool): If True and the file exists, it will be overwritten.
    """
    try:
        with open(file=f'{filename}', mode='x') as file:
            file.write(data)
    except FileNotFoundError:
        with open(file=f'{filename}', mode='w') as file:
            file.write(data)
    except FileExistsError:
        if overwrite:
            with open(file=f'{filename}', mode='w') as file:
                file.write(data)
        else:
            with open(file=f'{filename}', mode='a') as file:
                file.write(data)
    return data


def load_file(file_extension: str, file_path: str, data=None, full_load=True):
    """
    Loads data from a file of a given extension.
    Parameters:
        file_extension (str): The file extension of the file to load
        file_path (str): The file path of the file to load
        data (list | dict): Optional. The list or dictionary to update with the loaded data
    Returns:
        loaded_data (dict): The loaded data from the file.
    """
    loaded_data = None
    with open(file_path) as f:
        if file_extension == 'yaml':
            if full_load:
                loaded_data = yaml_full_load(f)
            else:
                loaded_data = yaml_load(
                    f,
                    Loader=yaml_loader
                )
        elif file_extension == 'json':
            loaded_data = load_json(f)
        elif file_extension == 'txt':
            loaded_data = f.read()
        elif file_extension == 'css':
            loaded_data = f.read()
        else:
            raise ValueError(f'Unsupported file extension {file_extension}')
    if data:
        data.update(loaded_data)
    else:
        data = loaded_data
    return data


