from typing import Sequence, List, Union, Dict, Set
from types import ModuleType

import os
from os import walk
from os.path import dirname, basename, join, splitext, isfile, exists
from importlib import import_module
from inspect import getmembers, isclass
import sys

from bpy.types import Operator, Panel, Menu, Preferences, PropertyGroup

class DuplicateAttributeError(Exception): pass

#このデコレータが付いている場合、そのクラスは無視されます。
def disable(cls: object) -> object:
    if hasattr(cls, 'addon_proc_disabled'): raise DuplicateAttributeError("The 'addon_proc_disabled' attribute is used in the 'disable' decorator.")
    cls.addon_proc_disabled = True # type: ignore
    return cls

#このデコレータで読み込みの優先順位を付けられます。付けられなかった場合は最後になります。
def priority(pr: int): # type: ignore
    def _priority(cls): # type: ignore
        if (hasattr(cls, 'addon_proc_priority')): raise DuplicateAttributeError("The 'addon_proc_priority' attribute is used in the 'priority' decorator.") # type: ignore
        cls.addon_proc_priority = pr # type: ignore
        return cls # type: ignore
    return _priority # type: ignore

def sendMsg(type: str, msg: str) -> None: print(f"{type}: {msg}")

class ProcLoader:
    #登録対象のクラス
    TARGET_CLASSES: object = (
        Operator,
        Panel,
        Menu,
        Preferences,
        PropertyGroup
    )

    def __init__(self, path: str) -> None:
        root = dirname(path) if isfile(path) else path #指定されたパスがファイルであれば最後のフォルダまでのパスを取得する
        self.__dir_name = basename(root) #アドオンのフォルダ名       例:addon_folder
        self.__path = dirname(root)      #アドオンフォルダまでのパス 例:path/to/blender/script/

        #モジュールの検索パスに登録する
        if self.__path not in sys.path:
            sys.path.append(self.__path)

    #モジュールとクラスを取得する
    def load(self, dirs: List[str]) -> List[Sequence[Union[ModuleType, object]]]:
        modules = self.load_modules(self.load_files(dirs))
        return [modules, self.load_classes(modules)]

    #[アドオン名].[フォルダ名].[ファイル名]の形でモジュール名を取得する
    def load_files(self, dirs: List[str]) -> List[str]:
        addon_path = join(self.__path, self.__dir_name) #アドオンへの絶対パス
        return self.__search_target_dirs(dirs, addon_path)

    #モジュールをインポートする
    @staticmethod
    def load_modules(paths: List[str]) -> List[ModuleType]:
        for path in paths:
            try:
                import_module(path)
            except ImportError or ModuleNotFoundError as e:
                sendMsg("Warning", f'Failed to load "{path}" module. \n {e}')

        return [import_module(mdl) for mdl in paths] # type: ignore

    #モジュール内のクラスを取得する
    @classmethod
    def load_classes(cls, modules: List[ModuleType]) -> List[object]:
        cls_priority: Dict[object, int] = {}

        for mdl in modules:
            for clazz in getmembers(mdl, isclass):
                clazz = clazz[1]
                #対象のクラスがアドオンのクラスかつ無効でない場合追加する
                if not any(issubclass(clazz, c) and not clazz == c for c in cls.TARGET_CLASSES): continue # type: ignore
                if hasattr(clazz, 'addon_proc_disabled') and clazz.addon_proc_disabled == True: continue # type: ignore

                #優先順位とクラスを辞書に追加する
                if hasattr(clazz, 'addon_proc_priority'): cls_priority[clazz] = clazz.addon_proc_priority # type: ignore
                else: cls_priority[clazz] = -1

        #優先順位を元にソートする(数が小さいほど先、-1(0以下)は最後)
        sorted_classes = sorted(cls_priority.items(), key=lambda item: float('inf') if item[1] < 0 else item[1])
        return [item[0] for item in sorted_classes]

    #検索対象のすべてのフォルダのモジュールを読み込む処理を行う
    def __search_target_dirs(self, dirs: List[str], addon_path: str) -> List[str]:
        modules: List[str] = []

        for dir in dirs:
            cur_path = join(addon_path, dir)
            if not exists(cur_path) or isfile(cur_path): raise NotADirectoryError(f'"{cur_path}" is not a folder or does not exist.')

            ignore_mdl = self.__read_ignore_module(cur_path) #指定したフォルダからの相対パスの形
            mdl_root = join(self.__dir_name, dir)            #[アドオンフォルダ]/[現在のフォルダ]

            modules += self.__search_all_sub_dirs(cur_path, mdl_root, dir, ignore_mdl)

        return modules

    #指定したフォルダのサブフォルダをすべて読み込み、無視リストとモジュールを取得する
    def __search_all_sub_dirs(self, cur_path: str, mdl_root: str, dir: str, ignore_mdl: Set[str]) -> List[str]:
        modules: List[str] = []

        for root, sub_dirs, files in walk(cur_path):
            if basename(root) == '__pycache__': continue #キャシュフォルダはスキップする
            if self.__is_ignore_module(root, dir, ignore_mdl): continue #モジュールが無視リストに入っている場合はスキップ

            ignore_mdl = ignore_mdl.union(self.__get_sub_ignore_folder(root, mdl_root, sub_dirs))

            modules += self.__get_all_modules(root, mdl_root, files, ignore_mdl)

        return modules

    #対象のすべてのファイルのモジュールパスを取得する
    def __get_all_modules(self, root: str, mdl_root: str, files: List[str], ignore_mdl: Set[str]) -> List[str]:
        modules: List[str] = []
        for file in files:
            abs_path = join(root, file) #ファイルまでの絶対パスを取得する
            if not isfile(abs_path) or not abs_path.endswith('.py'): continue #Pythonファイル以外は無視
            if file == '__init__.py': continue #初期化ファイルも無視

            mdl = self.__get_module_path(abs_path) #拡張子より左側だけをモジュールの形に変換する

            #アドオンフォルダ名と指定したフォルダ名を削除し、無視リストと比較する
            rel_mdl_path = self.__get_relative_module_path(mdl_root, mdl)
            if not rel_mdl_path in ignore_mdl: modules.append(mdl)

        return modules

    #サブフォルダの__init__.pyファイルから無視リストを取得する
    def __get_sub_ignore_folder(self, root: str, mdl_root: str, sub_dirs: List[str]) -> Set[str]:
        ignore_list: Set[str] = set([])
        for sub in sub_dirs:
            abs_path = join(root, sub)
            ignore = set([self.__get_relative_path(join(abs_path, item.replace('.', os.sep))) for item in self.__read_ignore_module(abs_path)]) #モジュールパスから相対パスを生成する
            ignore = set([self.__sep_to_period(item) for item in ignore if not basename(item.rstrip(os.sep)) == '__pycache__'])                  #相対パスをモジュールパスに変換し、システムフォルダを除外する
            ignore = set([self.__get_relative_module_path(mdl_root, item) for item in ignore])                                                   #相対モジュールパスを取得する

            ignore_list = ignore_list.union(ignore)

        return set(ignore_list)

    #各ディレクトリの__init__.pyファイルから無視リストを取得する
    def __read_ignore_module(self, cur_path: str) -> Set[str]:
        init_path = join(cur_path, '__init__.py')

        if not exists(init_path): return set([])

        init_mdl = import_module(self.__get_module_path(init_path))
        if hasattr(init_mdl, 'ignore'): return set(init_mdl.ignore)

        return set([])

    def __get_module_path(self, abs_path: str): return self.__conv_module_path(self.__get_relative_path(abs_path)) #import_module()関数に使えるモジュールパスを生成する(例：AddonName.operators.mdl)
    def __get_relative_path(self, abs_path: str): return abs_path.replace(self.__path, '').lstrip(os.sep)          #絶対パスを受け取り、アドオンフォルダからの相対パスを取得する
    def __conv_module_path(self, path: str): return self.__sep_to_period(splitext(path)[0])                        #ファイルパスをモジュールパスの形に変換する

    @staticmethod
    def __sep_to_period(string: str) -> str: return string.replace(os.sep, '.')

    @staticmethod
    def __get_relative_module_path(root: str ,path: str): return path.lstrip(root.replace(os.sep, '.')).lstrip('.') #アドオンフォルダ名と指定したフォルダ名を削除したモジュールパスを生成する

    #フォルダパスを整形して無視リストと比較する
    def __is_ignore_module(self, root: str, dir: str, ignore_module: Set[str]) -> bool:
        rel = self.__get_relative_path(root)
        rel_parts = rel.split(os.sep)
        try:
            rel = '.'.join(rel_parts[rel_parts.index(dir)+1:])
        except IndexError:
            rel = ""

        return not rel == "" and rel in ignore_module
