"""
 * ***** BEGIN GPL LICENSE BLOCK *****
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License
 * as published by the Free Software Foundation; either version 2
 * of the License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software Foundation,
 * Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
 *
 *
 * ***** END GPL LICENSE BLOCK *****
 """

# General imports
import os
import urllib
import requests
import threading
import time
from collections import OrderedDict

# For the wireframe export
import numpy as np
import base64

# Blender related
import bpy
import bpy.utils.previews
from bpy_extras.io_utils import ExportHelper
from bpy.props import (StringProperty,
                       EnumProperty,
                       BoolProperty,
                       IntProperty,
                       PointerProperty)

# glTF and addon related
from .io import *
from .sketchfab import Config, Utils, Cache
from .blender.imp.gltf2_blender_gltf import *
from .blender.blender_version import Version
from .io.exp import gltf2_io_draco_compression_extension


# Blender 2.79 has been shipped with openssl version 0.9.8 which uses a TLS protocol
# that is now blocked for security reasons on websites (github.com for example)
# In order to allow communication with github.com and other websites, the code will intend
# to use the updated openssl version distributed with the addon.
# Note: Blender 2.8 will have a more recent openssl version. This fix is only for 2.79 and olders
if bpy.app.version < (2, 80, 0) and not bpy.app.build_platform == b'Windows':
    try:
        sslib_path = None
        if bpy.app.build_platform == b'Darwin':
            sslib_path = os.path.join(os.path.dirname(__file__), 'dependencies/_ssl.cpython-35m-darwin.so')
        elif bpy.app.build_platform == b'Linux':
            sslib_path = os.path.join(os.path.dirname(__file__), '/io_sketchfab_plugin/_ssl.cpython-35m-x86_64-linux-gnu.so')

        import importlib.util
        spec = importlib.util.spec_from_file_location("_ssl", sslib_path)
        new_ssl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(new_ssl)


        from importlib import reload
        import ssl
        reload(ssl)
        from requests.packages.urllib3.util import ssl_
        reload(ssl_)
        print('SSL python module has been successfully overriden by Sketchfab addon')
        print('It might fix other addons having the same refused TLS protocol issue')
    except Exception as e:
        print(e)
        print("Failed to override SSL lib. The plugin will not be able to check for updates")


bl_info = {
    'name': 'Sketchfab Plugin',
    'description': 'Browse and download free Sketchfab downloadable models',
    'author': 'Sketchfab',
    'license': 'GPL',
    'deps': '',
    'version': (1, 1, 0),
    "blender": (2, 80, 0),
    'location': 'View3D > Tools > Sketchfab',
    'warning': '',
    'wiki_url': 'https://github.com/sketchfab/blender-plugin/releases',
    'tracker_url': 'https://github.com/sketchfab/blender-plugin/issues',
    'link': 'https://github.com/sketchfab/blender-plugin',
    'support': 'COMMUNITY',
    'category': 'Import-Export'
}
bl_info['blender'] = getattr(bpy.app, "version")

def get_version_string():
    return str(bl_info['version'][0]) + '.' + str(bl_info['version'][1]) + '.' + str(bl_info['version'][2])

PLUGIN_VERSION = str(bl_info['version']).strip('() ').replace(',', '.')
preview_collection = {}
is_plugin_enabled = False

# helpers
def get_sketchfab_login_props():
    return bpy.context.window_manager.sketchfab_api


def get_sketchfab_props():
    return bpy.context.window_manager.sketchfab_browser


def get_sketchfab_props_proxy():
    return bpy.context.window_manager.sketchfab_browser_proxy

def get_sketchfab_model(uid):
    skfb = get_sketchfab_props()
    return skfb.search_results['current'][uid]

def run_default_search():
    searchthr = GetRequestThread(Config.DEFAULT_SEARCH, parse_results)
    searchthr.start()


def get_plugin_enabled():
    props = get_sketchfab_props()
    return props.is_plugin_enabled


def refresh_search(self, context):
    pprops = get_sketchfab_props_proxy()
    if pprops.is_refreshing:
        return

    props = get_sketchfab_props()
    if pprops.own_models != props.own_models:
        pprops.is_refreshing = True
        props.own_models = pprops.own_models
        # restore all settings
        pprops.query = ""
        pprops.animated = False
        pprops.pbr = False
        pprops.staffpick = not props.own_models  # No sense to enable filter if own models
        pprops.categories = 'ALL'
        pprops.face_count = 'ANY'
        pprops.sort_by = 'RECENT'
        pprops.is_refreshing = False

    if 'current' in props.search_results:
        del props.search_results['current']

    props.query = pprops.query
    props.animated = pprops.animated
    props.pbr = pprops.pbr
    props.staffpick = pprops.staffpick
    props.categories = pprops.categories
    props.face_count = pprops.face_count
    props.sort_by = pprops.sort_by
    bpy.ops.wm.sketchfab_search('EXEC_DEFAULT')


def set_login_status(status_type, status):
    login_props = get_sketchfab_login_props()
    login_props.status = status
    login_props.status_type = status_type


def set_import_status(status):
    props = get_sketchfab_props()
    props.import_status = status


class SketchfabApi:
    def __init__(self):
        self.access_token = ''
        self.headers = {}
        self.username = ''
        self.display_name = ''
        self.plan_type = ''
        self.next_results_url = None
        self.prev_results_url = None

    def build_headers(self):
        self.headers = {'Authorization': 'Bearer ' + self.access_token}

    def login(self, email, password):
        bpy.ops.wm.login_modal('INVOKE_DEFAULT')

    def is_user_logged(self):
        if self.access_token and self.headers:
            return True

        return False

    def is_user_pro(self):
        return len(self.plan_type) and self.plan_type != 'basic'

    def logout(self):
        self.access_token = ''
        self.headers = {}
        Cache.delete_key('username')
        Cache.delete_key('access_token')
        Cache.delete_key('key')

    def request_user_info(self):
        requests.get(Config.SKETCHFAB_ME, headers=self.headers, hooks={'response': self.parse_user_info})

    def get_user_info(self):
        if self.display_name and self.plan_type:
            return '{} ({})'.format(self.display_name, self.plan_type)
        else:
            return ('', '')

    def parse_user_info(self, r, *args, **kargs):
        if r.status_code == 200:
            user_data = r.json()
            self.username = user_data['username']
            self.display_name = user_data['displayName']
            self.plan_type = user_data['account']
        else:
            print('Invalid access token')
            self.access_token = ''
            self.headers = {}

    def parse_login(self, r, *args, **kwargs):
        if r.status_code == 200 and 'access_token' in r.json():
            self.access_token = r.json()['access_token']
            self.build_headers()
            self.request_user_info()
        else:
            if 'error_description' in r.json():
                print("Failed to login: {}".format(r.json()['error_description']))
            else:
                print('Login failed.\n {}'.format(r.json()))

    def request_thumbnail(self, thumbnails_json):
        url = Utils.get_thumbnail_url(thumbnails_json)
        thread = ThumbnailCollector(url)
        thread.start()

    def request_model_info(self, uid):
        url = Config.SKETCHFAB_MODEL + '/' + uid
        model_infothr = GetRequestThread(url, self.handle_model_info)
        model_infothr.start()

    def handle_model_info(self, r, *args, **kwargs):
        skfb = get_sketchfab_props()
        uid = Utils.get_uid_from_model_url(r.url)

        # Dirty fix to avoid processing obsolete result data
        if 'current' not in skfb.search_results or uid not in skfb.search_results['current']:
            return

        model = skfb.search_results['current'][uid]
        json_data = r.json()
        model.license = json_data.get('license', {}).get('fullName', 'Personal (you own this model)')
        anim_count = int(json_data['animationCount'])
        model.animated = 'Yes ({} animation(s))'.format(anim_count) if anim_count > 0 else 'No'
        skfb.search_results['current'][uid] = model

    def search(self, query, search_cb):
        skfb = get_sketchfab_props()
        url = Config.BASE_SEARCH if not skfb.own_models else Config.BASE_SEARCH_OWN_MODELS

        search_query = '{}{}'.format(url, query)
        searchthr = GetRequestThread(search_query, search_cb, self.headers)
        searchthr.start()

    def search_cursor(self, url, search_cb):
        requests.get(url, headers=self.headers, hooks={'response': search_cb})

    def download_model(self, uid):
        skfb_model = get_sketchfab_model(uid)
        if skfb_model.download_url:
            # Check url sanity
            if time.time() - skfb_model.time_url_requested < skfb_model.url_expires:
                self.get_archive(skfb_model.download_url)
            else:
                print("Download url is outdated, requesting a new one")
                skfb_model.download_url = None
                skfb_model.url_expires = None
                skfb_model.time_url_requested = None
                requests.get(Utils.build_download_url(uid), headers=self.headers, hooks={'response': self.handle_download})
        else:
            requests.get(Utils.build_download_url(uid), headers=self.headers, hooks={'response': self.handle_download})

    def handle_download(self, r, *args, **kwargs):
        if r.status_code != 200 or 'gltf' not in r.json():
            print('Download not available for this model')
            return

        skfb = get_sketchfab_props()
        uid = Utils.get_uid_from_model_url(r.url)

        gltf = r.json()['gltf']
        skfb_model = get_sketchfab_model(uid)
        skfb_model.download_url = gltf['url']
        skfb_model.time_url_requested = time.time()
        skfb_model.url_expires = gltf['expires']

        self.get_archive(gltf['url'])

    def get_archive(self, url):
        if url is None:
            print('Url is None')
            return

        r = requests.get(url, stream=True)
        uid = Utils.get_uid_from_download_url(url)
        temp_dir = os.path.join(Config.SKETCHFAB_MODEL_DIR, uid)
        if not os.path.exists(temp_dir):
            os.makedirs(temp_dir)

        archive_path = os.path.join(temp_dir, '{}.zip'.format(uid))
        if not os.path.exists(archive_path):
            wm = bpy.context.window_manager
            wm.progress_begin(0, 100)
            set_log("Downloading model..")
            with open(archive_path, "wb") as f:
                total_length = r.headers.get('content-length')
                if total_length is None:  # no content length header
                    f.write(r.content)
                else:
                    dl = 0
                    total_length = int(total_length)
                    for data in r.iter_content(chunk_size=4096):
                        dl += len(data)
                        f.write(data)
                        done = int(100 * dl / total_length)
                        wm.progress_update(done)
                        set_log("Downloading model..{}%".format(done))

            wm.progress_end()
        else:
            print('Model already downloaded')

        gltf_path, gltf_zip = unzip_archive(archive_path)
        if gltf_path:
            try:
                import_model(gltf_path, uid)
            except Exception as e:
                import traceback
                print(traceback.format_exc())
        else:
            print("Failed to download model (url might be invalid)")
            model = get_sketchfab_model(uid)
            set_import_status("Import model ({})".format(model.download_size if model.download_size else 'fetching data'))


class SketchfabLoginProps(bpy.types.PropertyGroup):
    def update_tr(self, context):
        self.status = ''
        if self.email != self.last_username or self.password != self.last_password:
            self.last_username = self.email
            self.last_password = self.password
            if not self.password:
                set_login_status('ERROR', 'Password is empty')
            bpy.ops.wm.sketchfab_login('EXEC_DEFAULT')

    vars()["email"] = StringProperty(
        name="email",
        description="User email",
        default=""
    )


    vars()["password"] = StringProperty(
        name="password",
        description="User password",
        subtype='PASSWORD',
        default="",
        update=update_tr
    )

    vars()["access_token"] = StringProperty(
            name="access_token",
            description="oauth access token",
            subtype='PASSWORD',
            default=""
            )

    vars()["status"] = StringProperty(name='', default='')
    vars()["status_type"] = EnumProperty(
            name="Login status type",
            items=(('ERROR', "Error", ""),
                       ('INFO', "Information", ""),
                       ('FILE_REFRESH', "Progress", "")),
            description="Determines which icon to use",
            default='FILE_REFRESH'
            )

    vars()["last_username"] = StringProperty(default="default")
    vars()["last_password"] = StringProperty(default="default")

    skfb_api = SketchfabApi()


class SketchfabBrowserPropsProxy(bpy.types.PropertyGroup):
    # Search
    vars()["query"] = StringProperty(
            name="",
            update=refresh_search,
            description="Query to search",
            default="",
            options={'SKIP_SAVE'}
            )

    vars()["pbr"] = BoolProperty(
            name="PBR",
            description="Search for PBR model only",
            default=False,
            update=refresh_search,
            )

    vars()["categories"] = EnumProperty(
            name="Categories",
            items=Config.SKETCHFAB_CATEGORIES,
            description="Show only models of category",
            default='ALL',
            update=refresh_search
            )
    vars()["face_count"] = EnumProperty(
            name="Face Count",
            items=Config.SKETCHFAB_FACECOUNT,
            description="Determines which meshes are exported",
            default='ANY',
            update=refresh_search
            )

    vars()["sort_by"] = EnumProperty(
            name="Sort by",
            items=Config.SKETCHFAB_SORT_BY,
            description="Sort ",
            default='LIKES',
            update=refresh_search
            )

    vars()["animated"] = BoolProperty(
            name="Animated",
            description="Show only models with animation",
            default=False,
            update=refresh_search
            )

    vars()["staffpick"] = BoolProperty(
            name="Staffpick",
            description="Show only staffpick models",
            default=False,
            update=refresh_search
            )

    vars()["own_models"] = BoolProperty(
            name="My models (PRO)",
            description="Browse your own models (full library is only available for PRO account)",
            default=False,
            update=refresh_search
        )

    vars()["is_refreshing"] = BoolProperty(
        name="Refresh",
        description="Refresh",
        default=False,
    )

class SketchfabBrowserProps(bpy.types.PropertyGroup):
    # Search
    vars()["query"] = StringProperty(
            name="Search",
            description="Query to search",
            default=""
            )

    vars()["pbr"] = BoolProperty(
            name="PBR",
            description="Search for PBR model only",
            default=False
            )

    vars()["categories"] = EnumProperty(
            name="Categories",
            items=Config.SKETCHFAB_CATEGORIES,
            description="Show only models of category",
            default='ALL',
             )

    vars()["face_count"] = EnumProperty(
            name="Face Count",
            items=Config.SKETCHFAB_FACECOUNT,
            description="Determines which meshes are exported",
            default='ANY',
            )

    vars()["sort_by"] = EnumProperty(
            name="Sort by",
            items=Config.SKETCHFAB_SORT_BY,
            description="Sort ",
            default='LIKES',
            )

    vars()["animated"] = BoolProperty(
            name="Animated",
            description="Show only models with animation",
            default=False,
            )

    vars()["staffpick"] = BoolProperty(
            name="Staffpick",
            description="Show only staffpick models",
            default=False,
            )

    vars()["own_models"] = BoolProperty(
            name="My models (PRO)",
            description="Search within my models",
            default=False,
            update=refresh_search
        )

    vars()["status"] = StringProperty(name='status', default='idle')

    vars()["use_preview"] = BoolProperty(
        name="Use Preview",
        description="Show results using preview widget instead of regular buttons with thumbnails as icons",
        default=True
        )

    search_results = {}
    vars()["current_key"] = StringProperty(name='current', default='current')
    vars()["has_searched_next"] = BoolProperty(name='next', default=False)
    vars()["has_searched_prev"] = BoolProperty(name='prev', default=False)

    skfb_api = SketchfabLoginProps.skfb_api
    custom_icons = bpy.utils.previews.new()
    vars()["has_loaded_thumbnails"] = BoolProperty(default=False)

    vars()["is_latest_version"] = IntProperty(default=-1)

    vars()["import_status"] = StringProperty(name='import', default='')
    vars()["is_plugin_enabled"] = BoolProperty(default=False)


def list_current_results(self, context):
    skfb = get_sketchfab_props()

    # No results:
    if 'current' not in skfb.search_results:
        return preview_collection['default']

    if skfb.has_loaded_thumbnails and 'thumbnails' in preview_collection:
        return preview_collection['thumbnails']

    res = []
    missing_thumbnail = False
    if 'current' in skfb.search_results and len(skfb.search_results['current']):
        skfb_results = skfb.search_results['current']
        for i, result in enumerate(skfb_results):
            if result in skfb_results:
                model = skfb_results[result]
                if model.uid in skfb.custom_icons:
                    res.append((model.uid, model.title, "", skfb.custom_icons[model.uid].icon_id, i))
                else:
                    res.append((model.uid, model.title, "", preview_collection['skfb']['0'].icon_id, i))
                    missing_thumbnail = True
            else:
                print('Result issue')

    # Default element to avoid having an empty preview collection
    if not res:
        res.append(('NORESULTS', 'empty', "", preview_collection['skfb']['0'].icon_id, 0))

    preview_collection['thumbnails'] = tuple(res)
    skfb.has_loaded_thumbnails = not missing_thumbnail
    return preview_collection['thumbnails']


def draw_search(layout, context):
    props = get_sketchfab_props_proxy()
    skfb_api = get_sketchfab_props().skfb_api
    col = layout.box().column(align=True)
    col.prop(props, "query")
    ro = col.row();
    ownmodels_col = ro.column()
    ownmodels_col.enabled = skfb_api.is_user_logged();
    ownmodels_col.prop(props, "own_models")

    ro.operator("wm.sketchfab_search", text="Search", icon='VIEWZOOM')
    if props.own_models and skfb_api.is_user_logged() and not skfb_api.is_user_pro():
        layout.label(text='A PRO account is required to gain full API access', icon='QUESTION')
        layout.label(text='      to your personal library')

    pprops = get_sketchfab_props()


def draw_model_info(layout, model, context):
    ui_model_props = layout.box().column(align=True)
    ui_model_props.operator("wm.sketchfab_view", text="View on Sketchfab", icon='WORLD').model_uid = model.uid
    ui_model_props.label(text='{}'.format(model.title), icon='OBJECT_DATA')
    ui_model_props.label(text='{}'.format(model.author), icon='ARMATURE_DATA')

    if model.license:
        ui_model_props.label(text='{}'.format(model.license), icon='TEXT')
    else:
        ui_model_props.label(text='Fetching..')

    if model.vertex_count and model.face_count:
        ui_model_stats = ui_model_props.row()
        ui_model_stats.label(text='Verts: {}  |  Faces: {}'.format(Utils.humanify_number(model.vertex_count), Utils.humanify_number(model.face_count)), icon='MESH_DATA')

    if(model.animated):
        ui_model_props.label(text='Animated: ' + model.animated, icon='ANIM_DATA')

    import_ops = ui_model_props.column()
    skfb = get_sketchfab_props()
    import_ops.enabled = skfb.skfb_api.is_user_logged() and bpy.context.mode == 'OBJECT'
    if not skfb.skfb_api.is_user_logged():
        downloadlabel = 'You need to be logged in to download a model'
    elif bpy.context.mode != 'OBJECT':
        downloadlabel = "Import is available only in object mode"
    else:
        downloadlabel = "Import model ({})".format(model.download_size if model.download_size else 'fetching data')

    if skfb.import_status:
        downloadlabel = skfb.import_status

    download_icon = 'EXPORT' if import_ops.enabled else 'INFO'
    import_ops.label(text='')
    import_ops.operator("wm.sketchfab_download", icon=download_icon, text=downloadlabel, translate=False, emboss=True).model_uid = model.uid



def set_log(log):
    get_sketchfab_props().status = log

def unzip_archive(archive_path):
    if os.path.exists(archive_path):
        set_import_status('Unzipping model')
        import zipfile
        try:
            zip_ref = zipfile.ZipFile(archive_path, 'r')
            extract_dir = os.path.dirname(archive_path)
            zip_ref.extractall(extract_dir)
            zip_ref.close()
        except zipfile.BadZipFile:
            print('Error when dezipping file')
            os.remove(archive_path)
            print('Invaild zip. Try again')
            set_import_status('')
            return None, None

        gltf_file = os.path.join(extract_dir, 'scene.gltf')
        return gltf_file, archive_path

    else:
        print('ERROR: archive doesn\'t exist')


def run_async(func):
    from threading import Thread
    from functools import wraps

    @wraps(func)
    def async_func(*args, **kwargs):
        func_hl = Thread(target=func, args=args, kwargs=kwargs)
        func_hl.start()
        return func_hl

    return async_func


def import_model(gltf_path, uid):
    bpy.ops.wm.import_modal('INVOKE_DEFAULT', gltf_path=gltf_path, uid=uid)


def build_search_request(query, pbr, animated, staffpick, face_count, category, sort_by):
    final_query = '&q={}'.format(query)

    if animated:
        final_query = final_query + '&animated=true'

    if staffpick:
        final_query = final_query + '&staffpicked=true'

    if sort_by == 'LIKES':
        final_query = final_query + '&sort_by=-likeCount'
    elif sort_by == 'RECENT':
        final_query = final_query + '&sort_by=-publishedAt'
    elif sort_by == 'VIEWS':
        final_query = final_query + '&sort_by=-viewCount'

    if face_count == '10K':
        final_query = final_query + '&max_face_count=10000'
    elif face_count == '50K':
        final_query = final_query + '&min_face_count=10000&max_face_count=50000'
    elif face_count == '100K':
        final_query = final_query + '&min_face_count=50000&max_face_count=100000'
    elif face_count == '250K':
        final_query = final_query + "&min_face_count=100000&max_face_count=250000"
    elif face_count == '250KP':
        final_query = final_query + "&min_face_count=250000"

    if category != 'ALL':
        final_query = final_query + '&categories={}'.format(category)

    if pbr:
        final_query = final_query + '&pbr_type=metalness'

    return final_query


def parse_results(r, *args, **kwargs):
    skfb = get_sketchfab_props()
    json_data = r.json()

    if 'current' in skfb.search_results:
        skfb.search_results['current'].clear()
        del skfb.search_results['current']

    skfb.search_results['current'] = OrderedDict()

    for result in list(json_data['results']):

        # Dirty fix to avoid parsing obsolete data
        if 'current' not in skfb.search_results:
            return

        uid = result['uid']
        skfb.search_results['current'][result['uid']] = SketchfabModel(result)

        if not os.path.exists(os.path.join(Config.SKETCHFAB_THUMB_DIR, uid) + '.jpeg'):
            skfb.skfb_api.request_thumbnail(result['thumbnails'])
        elif uid not in skfb.custom_icons:
            skfb.custom_icons.load(uid, os.path.join(Config.SKETCHFAB_THUMB_DIR, "{}.jpeg".format(uid)), 'IMAGE')

    if json_data['next']:
        skfb.skfb_api.next_results_url = json_data['next']
    else:
        skfb.skfb_api.next_results_url = None

    if json_data['previous']:
        skfb.skfb_api.prev_results_url = json_data['previous']
    else:
        skfb.skfb_api.prev_results_url = None


"""
The two following classes are a direct copy from the Khronos addon,
  only adding an "export_wireframe" option to keep the topology information
  in the glTF files.

RUN with:
bpy.ops.wm.sketchfab_export(filepath="/home/loic/toto.gltf", export_format="GLTF_EMBEDDED", export_wireframe=True, export_extras=True)
"""

class ExportGLTF2_Base:
    # TODO: refactor to avoid boilerplate

    def __init__(self):
        self.is_draco_available = gltf2_io_draco_compression_extension.dll_exists()

    bl_options = {'UNDO', 'PRESET'}

    export_format = EnumProperty(
        name='Format',
        items=(('GLB', 'glTF Binary (.glb)',
                'Exports a single file, with all data packed in binary form. '
                'Most efficient and portable, but more difficult to edit later'),
               ('GLTF_EMBEDDED', 'glTF Embedded (.gltf)',
                'Exports a single file, with all data packed in JSON. '
                'Less efficient than binary, but easier to edit later'),
               ('GLTF_SEPARATE', 'glTF Separate (.gltf + .bin + textures)',
                'Exports multiple files, with separate JSON, binary and texture data. '
                'Easiest to edit later')),
        description=(
            'Output format and embedding options. Binary is most efficient, '
            'but JSON (embedded or separate) may be easier to edit later'
        ),
        default='GLB'
    )

    ui_tab = EnumProperty(
        items=(('GENERAL', "General", "General settings"),
               ('MESHES', "Meshes", "Mesh settings"),
               ('OBJECTS', "Objects", "Object settings"),
               ('ANIMATION', "Animation", "Animation settings")),
        name="ui_tab",
        description="Export setting categories",
    )

    export_copyright = StringProperty(
        name='Copyright',
        description='Legal rights and conditions for the model',
        default=''
    )

    export_image_format = EnumProperty(
        name='Images',
        items=(('NAME', 'Automatic',
                'Determine the image format from the blender image name'),
                ('JPEG', 'JPEG Format (.jpg)',
                'Encode and save textures as .jpg files. Be aware of a possible loss in quality'),
               ('PNG', 'PNG Format (.png)',
                'Encode and save textures as .png files')
               ),
        description=(
            'Output format for images. PNG is lossless and generally preferred, but JPEG might be preferable for web '
            'applications due to the smaller file size'
        ),
        default='NAME'
    )

    export_texcoords = BoolProperty(
        name='UVs',
        description='Export UVs (texture coordinates) with meshes',
        default=True
    )

    export_wireframe = BoolProperty(
        name='Wireframe',
        description='Export Wireframe for each primitive',
        default=True
    )

    export_normals = BoolProperty(
        name='Normals',
        description='Export vertex normals with meshes',
        default=True
    )

    export_draco_mesh_compression_enable = BoolProperty(
        name='Draco mesh compression',
        description='Compress mesh using Draco',
        default=False
    )

    export_draco_mesh_compression_level = IntProperty(
        name='Compression level',
        description='Compression level (0 = most speed, 6 = most compression, higher values currently not supported)',
        default=6,
        min=0,
        max=6
    )

    export_draco_position_quantization = IntProperty(
        name='Position quantization bits',
        description='Quantization bits for position values (0 = no quantization)',
        default=14,
        min=0,
        max=30
    )

    export_draco_normal_quantization = IntProperty(
        name='Normal quantization bits',
        description='Quantization bits for normal values (0 = no quantization)',
        default=10,
        min=0,
        max=30
    )

    export_draco_texcoord_quantization = IntProperty(
        name='Texcoord quantization bits',
        description='Quantization bits for texture coordinate values (0 = no quantization)',
        default=12,
        min=0,
        max=30
    )

    export_tangents = BoolProperty(
        name='Tangents',
        description='Export vertex tangents with meshes',
        default=False
    )

    export_materials = BoolProperty(
        name='Materials',
        description='Export materials',
        default=True
    )

    export_colors = BoolProperty(
        name='Vertex Colors',
        description='Export vertex colors with meshes',
        default=True
    )

    export_cameras = BoolProperty(
        name='Cameras',
        description='Export cameras',
        default=False
    )

    export_selected = BoolProperty(
        name='Selected Objects',
        description='Export selected objects only',
        default=False
    )

    export_extras = BoolProperty(
        name='Custom Properties',
        description='Export custom properties as glTF extras',
        default=False
    )

    export_yup = BoolProperty(
        name='+Y Up',
        description='Export using glTF convention, +Y up',
        default=True
    )

    export_apply = BoolProperty(
        name='Apply Modifiers',
        description='Apply modifiers (excluding Armatures) to mesh objects',
        default=False
    )

    export_animations = BoolProperty(
        name='Animations',
        description='Exports active actions and NLA tracks as glTF animations',
        default=True
    )

    export_frame_range = BoolProperty(
        name='Limit to Playback Range',
        description='Clips animations to selected playback range',
        default=True
    )

    export_frame_step = IntProperty(
        name='Sampling Rate',
        description='How often to evaluate animated values (in frames)',
        default=1,
        min=1,
        max=120
    )

    export_force_sampling = BoolProperty(
        name='Always Sample Animations',
        description='Apply sampling to all animations',
        default=False
    )

    export_current_frame = BoolProperty(
        name='Use Current Frame',
        description='Export the scene in the current animation frame',
        default=False
    )

    export_skins = BoolProperty(
        name='Skinning',
        description='Export skinning (armature) data',
        default=True
    )

    export_all_influences = BoolProperty(
        name='Include All Bone Influences',
        description='Allow >4 joint vertex influences. Models may appear incorrectly in many viewers',
        default=False
    )

    export_morph = BoolProperty(
        name='Shape Keys',
        description='Export shape keys (morph targets)',
        default=True
    )

    export_morph_normal = BoolProperty(
        name='Shape Key Normals',
        description='Export vertex normals with shape keys (morph targets)',
        default=True
    )

    export_morph_tangent = BoolProperty(
        name='Shape Key Tangents',
        description='Export vertex tangents with shape keys (morph targets)',
        default=False
    )

    export_lights = BoolProperty(
        name='Punctual Lights',
        description='Export directional, point, and spot lights. '
                    'Uses "KHR_lights_punctual" glTF extension',
        default=False
    )

    export_displacement = BoolProperty(
        name='Displacement Textures (EXPERIMENTAL)',
        description='EXPERIMENTAL: Export displacement textures. '
                    'Uses incomplete "KHR_materials_displacement" glTF extension',
        default=False
    )

    will_save_settings = BoolProperty(
        name='Remember Export Settings',
        description='Store glTF export settings in the Blender project',
        default=False)

    # Custom scene property for saving settings
    scene_key = "glTF2ExportSettings"

    # Helper functions
    def get_meshes_to_export(self, scene):
        """
        Returns a list of meshes to export, according to selection filters.
        For the first test, only considers the objects in the scene.
        Future should take view_layers, hidden objects... into consideration
        """
        TOEXPORT = []
        for o in scene.objects:
            if o.type=="MESH":
                if self.export_selected:
                    if bpy.app.version < (2, 80, 0):
                        if o.select:
                            TOEXPORT.append(o)
                    else:
                        if o.select_get():
                            TOEXPORT.append(o)
                else:
                    TOEXPORT.append(o)
        return TOEXPORT
    def activate_wireframe(self, obj):
        # Number of edges
        nEdges = len(obj.data.edge_keys)
        obj.data["nEdges"] = nEdges
        # Edges
        edges = np.array(obj.data.edge_keys, dtype=np.uint32).reshape((2*nEdges,))
        obj.data["wireframe"] = str(base64.b64encode(edges))
        # Data type (32bits uint)
        obj.data["componentType"] = 5125
    def deactivate_wireframe(self, obj):
        try: # Because sometimes things might not go as planned
            del obj.data["nEdges"]
            del obj.data["wireframe"]
            del obj.data["componentType"]
        except:
            print("Warning: Problem while deleting the custom properties for %s" % o.name)
    def save_settings(self, context):
        # find all export_ props
        all_props = self.properties
        export_props = {x: getattr(self, x) for x in dir(all_props)
                        if x.startswith("export_") and all_props.get(x) is not None}

        context.scene[self.scene_key] = export_props

    # INVOKE and EXECUTE, but no draw as only used in backend processing
    def invoke(self, context, event):
        settings = context.scene.get(self.scene_key)
        self.will_save_settings = False
        if settings:
            try:
                for (k, v) in settings.items():
                    setattr(self, k, v)
                self.will_save_settings = True

            except (AttributeError, TypeError):
                self.report({"ERROR"}, "Loading export settings failed. Removed corrupted settings")
                del context.scene[self.scene_key]

        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        import os
        import datetime
        from .blender.exp import gltf2_blender_export

        if self.will_save_settings:
            self.save_settings(context)

        if self.export_format == 'GLB':
            self.filename_ext = '.glb'
        else:
            self.filename_ext = '.gltf'

        # All custom export settings are stored in this container.
        export_settings = {}

        export_settings['timestamp'] = datetime.datetime.now()

        export_settings['gltf_filepath'] = bpy.path.ensure_ext(self.filepath, self.filename_ext)
        export_settings['gltf_filedirectory'] = os.path.dirname(export_settings['gltf_filepath']) + '/'

        export_settings['gltf_format'] = self.export_format
        export_settings['gltf_image_format'] = self.export_image_format
        export_settings['gltf_copyright'] = self.export_copyright
        export_settings['gltf_texcoords'] = self.export_texcoords
        export_settings['gltf_normals'] = self.export_normals
        export_settings['gltf_tangents'] = self.export_tangents and self.export_normals

        if self.is_draco_available:
            export_settings['gltf_draco_mesh_compression'] = self.export_draco_mesh_compression_enable
            export_settings['gltf_draco_mesh_compression_level'] = self.export_draco_mesh_compression_level
            export_settings['gltf_draco_position_quantization'] = self.export_draco_position_quantization
            export_settings['gltf_draco_normal_quantization'] = self.export_draco_normal_quantization
            export_settings['gltf_draco_texcoord_quantization'] = self.export_draco_texcoord_quantization
        else:
            export_settings['gltf_draco_mesh_compression'] = False

        export_settings['gltf_materials'] = self.export_materials
        export_settings['gltf_colors'] = self.export_colors
        export_settings['gltf_cameras'] = self.export_cameras
        export_settings['gltf_selected'] = self.export_selected
        export_settings['gltf_layers'] = True  # self.export_layers
        export_settings['gltf_extras'] = self.export_extras
        export_settings['gltf_yup'] = self.export_yup
        export_settings['gltf_apply'] = self.export_apply
        export_settings['gltf_current_frame'] = self.export_current_frame
        export_settings['gltf_animations'] = self.export_animations
        if self.export_animations:
            export_settings['gltf_frame_range'] = self.export_frame_range
            export_settings['gltf_force_sampling'] = self.export_force_sampling
        else:
            export_settings['gltf_frame_range'] = False
            export_settings['gltf_move_keyframes'] = False
            export_settings['gltf_force_sampling'] = False
        export_settings['gltf_skins'] = self.export_skins
        if self.export_skins:
            export_settings['gltf_all_vertex_influences'] = self.export_all_influences
        else:
            export_settings['gltf_all_vertex_influences'] = False
        export_settings['gltf_frame_step'] = self.export_frame_step
        export_settings['gltf_morph'] = self.export_morph
        if self.export_morph:
            export_settings['gltf_morph_normal'] = self.export_morph_normal
        else:
            export_settings['gltf_morph_normal'] = False
        if self.export_morph and self.export_morph_normal:
            export_settings['gltf_morph_tangent'] = self.export_morph_tangent
        else:
            export_settings['gltf_morph_tangent'] = False

        export_settings['gltf_lights'] = self.export_lights
        export_settings['gltf_displacement'] = self.export_displacement

        export_settings['gltf_binary'] = bytearray()
        export_settings['gltf_binaryfilename'] = os.path.splitext(os.path.basename(
            bpy.path.ensure_ext(self.filepath,self.filename_ext)))[0] + '.bin'

        # Exporting the object, with the wireframe
        if self.export_wireframe:
            to_export = self.get_meshes_to_export(context.scene)
            for o in to_export:
                self.activate_wireframe(o)
            res = gltf2_blender_export.save(context, export_settings)
            for o in to_export:
                self.deactivate_wireframe(o)
            return res
        # Or without the wireframe (fallback on the original method)
        else:
            return gltf2_blender_export.save(context, export_settings)

class ExportGLTF2(bpy.types.Operator, ExportGLTF2_Base, ExportHelper):
    """Export scene as glTF for Sketchfab processing"""
    bl_idname = 'wm.sketchfab_export'
    bl_label = 'Export glTF 2.0'

    filename_ext = ''

    filter_glob = StringProperty(default='*.glb;*.gltf', options={'HIDDEN'})


class ThumbnailCollector(threading.Thread):
    def __init__(self, url):
        self.url = url
        threading.Thread.__init__(self)

    def set_url(self, url):
        self.url = url

    def run(self):
        if not self.url:
            return
        requests.get(self.url, stream=True, hooks={'response': self.handle_thumbnail})

    def handle_thumbnail(self, r, *args, **kwargs):
        uid = r.url.split('/')[4]
        if not os.path.exists(Config.SKETCHFAB_THUMB_DIR):
            try:
                os.makedirs(Config.SKETCHFAB_THUMB_DIR)
            except:
                pass
        thumbnail_path = os.path.join(Config.SKETCHFAB_THUMB_DIR, uid) + '.jpeg'

        with open(thumbnail_path, "wb") as f:
            total_length = r.headers.get('content-length')

            if total_length is None and r.content:
                f.write(r.content)
            else:
                dl = 0
                total_length = int(total_length)
                for data in r.iter_content(chunk_size=4096):
                    dl += len(data)
                    f.write(data)

        props = get_sketchfab_props()
        if uid not in props.custom_icons:
            props.custom_icons.load(uid, os.path.join(Config.SKETCHFAB_THUMB_DIR, "{}.jpeg".format(uid)), 'IMAGE')


class LoginModal(bpy.types.Operator):
    bl_idname = "wm.login_modal"
    bl_label = "Import glTF model into Sketchfab"
    bl_options = {'INTERNAL'}

    is_logging = BoolProperty(default=False)
    error = BoolProperty(default=False)
    error_message = StringProperty(default='')

    def exectue(self, context):
        return {'FINISHED'}

    def handle_login(self, r, *args, **kwargs):
        browser_props = get_sketchfab_props()
        if r.status_code == 200 and 'access_token' in r.json():
            browser_props.skfb_api.access_token = r.json()['access_token']
            login_props = get_sketchfab_login_props()
            Cache.save_key('username', login_props.email)
            Cache.save_key('access_token', browser_props.skfb_api.access_token)

            browser_props.skfb_api.build_headers()
            set_login_status('INFO', '')
            browser_props.skfb_api.request_user_info()

        else:
            if 'error_description' in r.json():
                set_login_status('ERROR', 'Failed to authenticate: bad login/password')
            else:
                set_login_status('ERROR', 'Failed to authenticate: bad login/password')
                print('Cannot login.\n {}'.format(r.json()))

        self.is_logging = False

    def modal(self, context, event):
        if self.error:
            self.error = False
            set_login_status('ERROR', '{}'.format(self.error_message))
            return {"FINISHED"}

        if self.is_logging:
            set_login_status('FILE_REFRESH', 'Loging in to your Sketchfab account...')
            return {'RUNNING_MODAL'}
        else:
            return {'FINISHED'}

    def invoke(self, context, event):
        self.is_logging = True
        try:
            context.window_manager.modal_handler_add(self)
            login_props = get_sketchfab_login_props()
            url = '{}&username={}&password={}'.format(Config.SKETCHFAB_OAUTH, urllib.parse.quote_plus(login_props.email), urllib.parse.quote_plus(login_props.password))
            requests.post(url, hooks={'response': self.handle_login})
        except Exception as e:
            self.error = True
            self.error_message = str(e)

        return {'RUNNING_MODAL'}


class ImportModalOperator(bpy.types.Operator):
    bl_idname = "wm.import_modal"
    bl_label = "Import glTF model into Sketchfab"
    bl_options = {'INTERNAL'}

    gltf_path = StringProperty()
    uid = StringProperty()

    def exectue(self, context):
        print('IMPORT')
        return {'FINISHED'}

    def modal(self, context, event):
        bpy.context.scene.render.engine = Version.ENGINE
        gltf_importer = glTFImporter(self.gltf_path, Log.default())
        success, txt = gltf_importer.read()
        if not success:
            print('Failed to read GLTF')
        try:
            BlenderGlTF.create(gltf_importer, root_name=Utils.make_model_name(gltf_importer.data))
            set_import_status('')
            Utils.clean_downloaded_model_dir(self.uid)
            return {'FINISHED'}
        except Exception:
            import traceback
            print(traceback.format_exc())
            set_import_status('')
            return {'FINISHED'}

        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        context.window_manager.modal_handler_add(self)
        set_import_status('Importing...')
        return {'RUNNING_MODAL'}


class GetRequestThread(threading.Thread):
    def __init__(self, url, callback, headers={}):
        self.url = url
        self.callback = callback
        self.headers = headers
        threading.Thread.__init__(self)

    def run(self):
        requests.get(self.url, headers=self.headers, hooks={'response': self.callback})


class View3DPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'TOOLS'
    bl_label = "Sketchfab Assets Browser"
    bl_context = 'objectmode'
    bl_category = "Sketchfab" if bpy.app.version == (2, 79, 0) else ""

    @classmethod
    def poll(cls, context):
        return (context.scene is not None)


class LoginPanel(View3DPanel, bpy.types.Panel):
    bl_idname = "VIEW3D_PT_Login"
    bl_label = "Log in to your Sketchfab account"

    is_logged = BoolProperty()

    def draw(self, context):
        skfb = get_sketchfab_props()

        if not skfb.is_plugin_enabled:
            self.layout.operator('wm.skfb_enable', text='Connect to Sketchfab', icon='LINKED').enable = True
        else:
            # LOGIN
            skfb_login = get_sketchfab_login_props()
            layout = self.layout.box().column(align=True)
            layout.enabled = get_plugin_enabled()
            if skfb_login.skfb_api.is_user_logged():
                login_row = layout.row()
                login_row.label(text='Logged in as {}'.format(skfb_login.skfb_api.get_user_info()))
                login_row.operator('wm.sketchfab_login', text='Logout', icon='DISCLOSURE_TRI_RIGHT').authenticate = False
                if skfb_login.status:
                    layout.prop(skfb_login, 'status', icon=skfb_login.status_type)
            else:
                layout.label(text="Login to your Sketchfab account", icon='INFO')
                layout.prop(skfb_login, "email")
                layout.prop(skfb_login, "password")
                ops_row = layout.row()
                ops_row.operator('wm.sketchfab_signup', text='Create an account', icon='PLUS')
                ops_row.operator('wm.sketchfab_login', text='Log in', icon='WORLD').authenticate = True
                if skfb_login.status:
                    layout.prop(skfb_login, 'status', icon=skfb_login.status_type)

            # Version info
            self.layout.separator()
            if skfb.is_latest_version == 1:
                self.bl_label = "Sketchfab plugin v{} (up-to-date)".format(PLUGIN_VERSION)
            elif skfb.is_latest_version == 0:
                self.bl_label = "Sketchfab plugin v{} (outdated)".format(PLUGIN_VERSION)
                self.layout.operator('wm.skfb_new_version', text='New version available', icon='ERROR')
            elif skfb.is_latest_version == -2:
                self.bl_label = "Sketchfab plugin v{}".format(PLUGIN_VERSION)

        # External links
        doc_ui = self.layout.row()
        doc_ui.operator('wm.skfb_help', text='Documentation', icon='QUESTION')
        doc_ui.operator('wm.skfb_report_issue', text='Report an issue', icon='ERROR')
        self.layout.label(text=Config.SKETCHFAB_TEMP_DIR, icon="SCRIPT")

        layout = self.layout

class FiltersPanel(View3DPanel, bpy.types.Panel):
    bl_idname = "VIEW3D_PT_sketchfab_filters"
    bl_label = "Search filters"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.enabled = get_plugin_enabled()
        props = get_sketchfab_props_proxy()

        col = layout.box().column(align=True)

        col.prop(props, "pbr")
        col.prop(props, "staffpick")
        col.prop(props, "animated")

        col.separator()
        col.prop(props, "categories")

        col.label(text='Sort by')
        sb = col.row()
        sb.prop(props, "sort_by", expand=True)

        col.label(text='Face count')
        col.prop(props, "face_count", expand=True)


def draw_results_icons(results, props, nbcol=4):
    props = get_sketchfab_props()
    current = props.search_results['current']

    dimx = nbcol if current else 0
    dimy = int(24 / nbcol) if current else 0
    if dimx is not 0 and dimy is not 0:
        for r in range(dimy):
            ro = results.row()
            for col in range(dimx):
                col2 = ro.column(align=True)
                index = r * dimx + col
                if index >= len(current.keys()):
                    return

                model = current[list(current.keys())[index]]

                if model.uid in props.custom_icons:
                    col2.operator("wm.sketchfab_modelview", icon_value=props.custom_icons[model.uid].icon_id, text="{}".format(model.title + ' by ' + model.author)).uid = list(current.keys())[index]
                else:
                    col2.operator("wm.sketchfab_modelview", text="{}".format(model.title + ' by ' + model.author)).uid = list(current.keys())[index]
    else:
        results.row()
        results.row().label(text='No results')
        results.row()


class ResultsPanel(View3DPanel, bpy.types.Panel):
    bl_idname = "VIEW3D_PT_sketchfab_results"
    bl_label = "Results"

    uid = ''

    def draw(self, context):
        self.layout.enabled =  get_plugin_enabled()

        layout = self.layout
        col = layout.column(align=True)
        props = get_sketchfab_props()
        results = layout.column(align=True)
        model = None

        result_pages_ops = col.row()
        if props.skfb_api.prev_results_url:
            result_pages_ops.operator("wm.sketchfab_search_prev", text="Previous page", icon='FRAME_PREV')

        if props.skfb_api.next_results_url:
            result_pages_ops.operator("wm.sketchfab_search_next", text="Next page", icon='FRAME_NEXT')

        result_label = 'Click below to see more results'

        col.label(text=result_label, icon='INFO')
        try:
            col.template_icon_view(bpy.context.window_manager, 'result_previews', show_labels=True, scale=5.0)
        except Exception:
            print('ResultsPanel: Failed to display results')
            pass

        if 'current' not in props.search_results or not len(props.search_results['current']):
            self.bl_label = 'No results'
            return
        else:
            self.bl_label = "Results"

        if bpy.context.window_manager.result_previews not in props.search_results['current']:
            return

        model = props.search_results['current'][bpy.context.window_manager.result_previews]

        if not model:
            return

        if self.uid != model.uid:
            self.uid = model.uid

            if not model.info_requested:
                props.skfb_api.request_model_info(model.uid)
                model.info_requested = True

        draw_model_info(col, model, context)


class SketchfabLogger(bpy.types.Operator):
    bl_idname = 'wm.sketchfab_login'
    bl_label = 'Sketchfab Login'
    bl_options = {'INTERNAL'}

    authenticate = BoolProperty(default=True)

    def execute(self, context):
        set_login_status('FILE_REFRESH', 'Login to your Sketchfab account...')
        wm = context.window_manager
        if self.authenticate:
            wm.sketchfab_browser.skfb_api.login(wm.sketchfab_api.email, wm.sketchfab_api.password)
        else:
            wm.sketchfab_browser.skfb_api.logout()
            wm.sketchfab_api.password = ''
            wm.sketchfab_api.last_password = "default"
            set_login_status('FILE_REFRESH', '')
        return {'FINISHED'}


# Operator to perform search on Sketchfab
class SketchfabBrowse(View3DPanel, bpy.types.Panel):
    bl_idname = "wm.sketchfab_browse" if bpy.app.version == (2, 79, 0) else "VIEW3D_PT_sketchfab_browse"
    bl_label = "Search"

    def draw(self, context):
        self.layout.enabled = get_plugin_enabled()

        draw_search(self.layout, context)

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_props_dialog(self, width=900, height=850)


class SketchfabModel:
    def __init__(self, json_data):
        self.title = str(json_data['name'])
        self.author = json_data['user']['displayName']
        self.uid = json_data['uid']
        self.vertex_count = json_data['vertexCount']
        self.face_count = json_data['faceCount']

        if 'archives' in json_data and  'gltf' in json_data['archives']:
            if 'size' in json_data['archives']['gltf'] and json_data['archives']['gltf']['size']:
                self.download_size = Utils.humanify_size(json_data['archives']['gltf']['size'])
        else:
            self.download_size = None

        self.thumbnail_url = os.path.join(Config.SKETCHFAB_THUMB_DIR, '{}.jpeg'.format(self.uid))

        # Model info request
        self.info_requested = False
        self.license = None
        self.animated = False

        # Download url data
        self.download_url = None
        self.time_url_requested = None
        self.url_expires = None


class SketchfabDownloadModel(bpy.types.Operator):
    bl_idname = "wm.sketchfab_download"
    bl_label = "Downloading"
    bl_options = {'INTERNAL'}

    model_uid = bpy.props.StringProperty(name="uid")

    def execute(self, context):
        skfb_api = context.window_manager.sketchfab_browser.skfb_api
        skfb_api.download_model(self.model_uid)
        return {'FINISHED'}


class ViewOnSketchfab(bpy.types.Operator):
    bl_idname = "wm.sketchfab_view"
    bl_label = "Open on Browser"
    bl_options = {'INTERNAL'}

    model_uid = bpy.props.StringProperty(name="uid")

    def execute(self, context):
        import webbrowser
        webbrowser.open('{}/models/{}'.format(Config.SKETCHFAB_URL, self.model_uid))
        return {'FINISHED'}


def clear_search():
    skfb = get_sketchfab_props()
    skfb.has_loaded_thumbnails = False
    skfb.search_results.clear()
    skfb.custom_icons.clear()
    bpy.data.window_managers['WinMan']['result_previews'] = 0


class SketchfabSearch(bpy.types.Operator):
    bl_idname = "wm.sketchfab_search"
    bl_label = "Search Sketchfab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        # prepare request for search
        clear_search()
        skfb = get_sketchfab_props()
        skfb.skfb_api.prev_results_url = None
        skfb.skfb_api.next_results_url = None
        final_query = build_search_request(skfb.query, skfb.pbr, skfb.animated, skfb.staffpick, skfb.face_count, skfb.categories, skfb.sort_by)
        skfb.skfb_api.search(final_query, parse_results)
        return {'FINISHED'}


class SketchfabSearchNextResults(bpy.types.Operator):
    bl_idname = "wm.sketchfab_search_next"
    bl_label = "Search Sketchfab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        # prepare request for search
        clear_search()
        skfb_api = get_sketchfab_props().skfb_api
        skfb_api.search_cursor(skfb_api.next_results_url, parse_results)
        return {'FINISHED'}


class SketchfabSearchPreviousResults(bpy.types.Operator):
    bl_idname = "wm.sketchfab_search_prev"
    bl_label = "Search Sketchfab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        # prepare request for search
        clear_search()
        skfb_api = get_sketchfab_props().skfb_api
        skfb_api.search_cursor(skfb_api.prev_results_url, parse_results)
        return {'FINISHED'}


class SketchfabOpenModel(bpy.types.Operator):
    bl_idname = "wm.sketchfab_open"
    bl_label = "Downloading"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        col.label(text="I'm downloading your model!")

    def invoke(self, context, event):
        wm = context.window_manager
        return wm.invoke_popup(self, width=550)


class SketchfabCreateAccount(bpy.types.Operator):
    bl_idname = "wm.sketchfab_signup"
    bl_label = "Sketchfab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        import webbrowser
        webbrowser.open(Config.SKETCHFAB_SIGNUP)
        return {'FINISHED'}


class SketchfabNewVersion(bpy.types.Operator):
    bl_idname = "wm.skfb_new_version"
    bl_label = "Sketchfab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        import webbrowser
        webbrowser.open('{}/releases/latest'.format(Config.GITHUB_REPOSITORY_URL))
        return {'FINISHED'}


class SketchfabReportIssue(bpy.types.Operator):
    bl_idname = "wm.skfb_report_issue"
    bl_label = "Sketchfab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        import webbrowser
        webbrowser.open(Config.SKETCHFAB_REPORT_URL)
        return {'FINISHED'}


class SketchfabHelp(bpy.types.Operator):
    bl_idname = "wm.skfb_help"
    bl_label = "Sketchfab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        import webbrowser
        webbrowser.open('{}/releases/latest'.format(Config.GITHUB_REPOSITORY_URL))
        return {'FINISHED'}


def activate_plugin():
    props = get_sketchfab_props()
    login = get_sketchfab_login_props()

    # Fill login/access_token
    cache_data = Cache.read()
    if 'username' in cache_data:
        login.email = cache_data['username']

    if 'access_token' in cache_data:
        props.skfb_api.access_token = cache_data['access_token']
        props.skfb_api.build_headers()
        props.skfb_api.request_user_info()

    props.is_plugin_enabled = True

    try:
        requests.get(Config.SKETCHFAB_PLUGIN_VERSION, hooks={'response': check_plugin_version})
    except Exception as e:
        print('Error when checking for version: {}'.format(e))

    run_default_search()


class SketchfabEnable(bpy.types.Operator):
    bl_idname = "wm.skfb_enable"
    bl_label = "Sketchfab"
    bl_options = {'INTERNAL'}

    enable = BoolProperty(default=True)
    def execute(self, context):
        if self.enable:
            activate_plugin()

        return {'FINISHED'}


classes = (
    # Properties
    SketchfabBrowserProps,
    SketchfabLoginProps,
    SketchfabBrowserPropsProxy,

    # Panels
    LoginPanel,
    SketchfabBrowse,
    FiltersPanel,
    ResultsPanel,

    # Operators
    SketchfabEnable,
    SketchfabCreateAccount,
    LoginModal,
    SketchfabNewVersion,
    SketchfabHelp,
    SketchfabReportIssue,
    SketchfabSearch,
    SketchfabSearchPreviousResults,
    SketchfabSearchNextResults,
    ImportModalOperator,
    ViewOnSketchfab,
    SketchfabDownloadModel,
    SketchfabLogger,
    ExportGLTF2,
    )

def check_plugin_version(request, *args, **kwargs):
    response = request.json()
    skfb = get_sketchfab_props()
    if response and len(response):
        latest_release_version = response[0]['tag_name'].replace('.', '')
        current_version = str(bl_info['version']).replace(',', '').replace('(', '').replace(')', '').replace(' ', '')

        if latest_release_version == current_version:
            print('You are using the latest version({})'.format(response[0]['tag_name']))
            skfb.is_latest_version = 1
        else:
            print('A new version is available: {}'.format(response[0]['tag_name']))
            skfb.is_latest_version = 0
    else:
        print('Failed to retrieve plugin version')
        skfb.is_latest_version = -2

def register():
    sketchfab_icon = bpy.utils.previews.new()
    sketchfab_icon.load("skfb", "D:\\logo.png", 'IMAGE')
    sketchfab_icon.load("0", "D:\\placeholder.png", 'IMAGE')

    res = []
    res.append(('NORESULTS', 'empty', "", sketchfab_icon['0'].icon_id, 0))
    preview_collection['default'] = tuple(res)
    preview_collection['skfb'] = sketchfab_icon
    bpy.types.WindowManager.result_previews = EnumProperty(items=list_current_results)

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.WindowManager.sketchfab_browser = PointerProperty(
                type=SketchfabBrowserProps)

    bpy.types.WindowManager.sketchfab_browser_proxy = PointerProperty(
                type=SketchfabBrowserPropsProxy)

    bpy.types.WindowManager.sketchfab_api = PointerProperty(
                type=SketchfabLoginProps,
                )

def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)

    del bpy.types.WindowManager.sketchfab_api
    del bpy.types.WindowManager.sketchfab_browser
    del bpy.types.WindowManager.sketchfab_browser_proxy
    bpy.utils.previews.remove(preview_collection['skfb'])
    del bpy.types.WindowManager.result_previews
    Utils.clean_thumbnail_directory()


if __name__ == "__main__":
    register()
