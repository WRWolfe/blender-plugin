# Copyright 2018-2019 The glTF-Blender-IO authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import bpy
from typing import Optional, Dict, List, Any, Tuple
from .gltf2_blender_export_keys import MORPH
from .gltf2_blender_gather_cache import cached
from ...io.com import gltf2_io
from . import gltf2_blender_gather_primitives
from . import gltf2_blender_generate_extras
from ...io.com.gltf2_io_debug import print_console


@cached
def gather_mesh(blender_mesh: bpy.types.Mesh,
                vertex_groups: Optional[bpy.types.VertexGroups],
                modifiers: Optional[bpy.types.ObjectModifiers],
                skip_filter: bool,
                material_names: Tuple[str],
                export_settings
                ) -> Optional[gltf2_io.Mesh]:
    if not skip_filter and not __filter_mesh(blender_mesh, vertex_groups, modifiers, export_settings):
        return None

    mesh = gltf2_io.Mesh(
        extensions=__gather_extensions(blender_mesh, vertex_groups, modifiers, export_settings),
        extras=__gather_extras(blender_mesh, vertex_groups, modifiers, export_settings),
        name=__gather_name(blender_mesh, vertex_groups, modifiers, export_settings),
        primitives=__gather_primitives(blender_mesh, vertex_groups, modifiers, material_names, export_settings),
        weights=__gather_weights(blender_mesh, vertex_groups, modifiers, export_settings)
    )

    if len(mesh.primitives) == 0:
        print_console("WARNING", "Mesh '{}' has no primitives and will be omitted.".format(mesh.name))
        return None
    return mesh


def __filter_mesh(blender_mesh: bpy.types.Mesh,
                  vertex_groups: Optional[bpy.types.VertexGroups],
                  modifiers: Optional[bpy.types.ObjectModifiers],
                  export_settings
                  ) -> bool:

    if blender_mesh.users == 0:
        return False
    return True


def __gather_extensions(blender_mesh: bpy.types.Mesh,
                        vertex_groups: Optional[bpy.types.VertexGroups],
                        modifiers: Optional[bpy.types.ObjectModifiers],
                        export_settings
                        ) -> Any:
    return None


def __gather_extras(blender_mesh: bpy.types.Mesh,
                    vertex_groups: Optional[bpy.types.VertexGroups],
                    modifiers: Optional[bpy.types.ObjectModifiers],
                    export_settings
                    ) -> Optional[Dict[Any, Any]]:

    extras = {}

    if export_settings['gltf_extras']:
        extras = gltf2_blender_generate_extras.generate_extras(blender_mesh) or {}

    if export_settings[MORPH] and blender_mesh.shape_keys:
        morph_max = len(blender_mesh.shape_keys.key_blocks) - 1
        if morph_max > 0:
            target_names = []
            for blender_shape_key in blender_mesh.shape_keys.key_blocks:
                if blender_shape_key != blender_shape_key.relative_key:
                    target_names.append(blender_shape_key.name)
            extras['targetNames'] = target_names

    if extras:
        return extras

    return None


def __gather_name(blender_mesh: bpy.types.Mesh,
                  vertex_groups: Optional[bpy.types.VertexGroups],
                  modifiers: Optional[bpy.types.ObjectModifiers],
                  export_settings
                  ) -> str:
    return blender_mesh.name


def __gather_primitives(blender_mesh: bpy.types.Mesh,
                        vertex_groups: Optional[bpy.types.VertexGroups],
                        modifiers: Optional[bpy.types.ObjectModifiers],
                        material_names: Tuple[str],
                        export_settings
                        ) -> List[gltf2_io.MeshPrimitive]:
    return gltf2_blender_gather_primitives.gather_primitives(blender_mesh,
                                                             vertex_groups,
                                                             modifiers,
                                                             material_names,
                                                             export_settings)


def __gather_weights(blender_mesh: bpy.types.Mesh,
                     vertex_groups: Optional[bpy.types.VertexGroups],
                     modifiers: Optional[bpy.types.ObjectModifiers],
                     export_settings
                     ) -> Optional[List[float]]:

    if not export_settings[MORPH] or not blender_mesh.shape_keys:
        return None

    morph_max = len(blender_mesh.shape_keys.key_blocks) - 1
    if morph_max <= 0:
        return None

    weights = []

    for blender_shape_key in blender_mesh.shape_keys.key_blocks:
        if blender_shape_key != blender_shape_key.relative_key:
            weights.append(blender_shape_key.value)

    return weights
