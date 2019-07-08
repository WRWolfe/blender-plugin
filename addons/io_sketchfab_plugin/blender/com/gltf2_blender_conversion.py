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
 * Contributor(s): Julien Duroure.
 *
 * ***** END GPL LICENSE BLOCK *****
 """

from mathutils import Matrix, Vector, Quaternion

class Conversion():

    @staticmethod
    def matrix_gltf_to_blender(mat_input):
        mat =  Matrix([mat_input[0:4], mat_input[4:8], mat_input[8:12], mat_input[12:16]])
        mat.transpose()
        return mat

    @staticmethod
    def loc_gltf_to_blender(loc):
        return loc

    @staticmethod
    def scale_gltf_to_blender(scale):
        return scale

    @staticmethod
    def quaternion_gltf_to_blender(q):
        return Quaternion([q[3], q[0], q[1], q[2]])

    def scale_to_matrix(scale):
        mat = Matrix()
        for i in range(3):
            mat[i][i] = scale[i]

        return mat


def correction_rotation():
    """Correction of Rotation."""
    # Correction is needed for lamps, because Yup2Zup is not written in vertices
    # and lamps has no vertices :)
    return Quaternion((sqrt(2)/2, -sqrt(2)/2, 0.0, 0.0)).to_matrix().to_4x4()

def texture_transform_blender_to_gltf(mapping_transform):
    """
    Converts the offset/rotation/scale from a Mapping node applied in Blender's
    UV space to the equivalent KHR_texture_transform.
    """
    offset = mapping_transform.get('offset', [0, 0])
    rotation = mapping_transform.get('rotation', 0)
    scale = mapping_transform.get('scale', [1, 1])
    return {
        'offset': [
            offset[0] - scale[1] * sin(rotation),
            1 - offset[1] - scale[1] * cos(rotation),
        ],
        'rotation': rotation,
        'scale': [scale[0], scale[1]],
    }

def texture_transform_gltf_to_blender(texture_transform):
    """
    Converts a KHR_texture_transform into the equivalent offset/rotation/scale
    for a Mapping node applied in Blender's UV space.
    """
    offset = texture_transform.get('offset', [0, 0])
    rotation = texture_transform.get('rotation', 0)
    scale = texture_transform.get('scale', [1, 1])
    return {
        'offset': [
            offset[0] + scale[1] * sin(rotation),
            1 - offset[1] - scale[1] * cos(rotation),
        ],
        'rotation': rotation,
        'scale': [scale[0], scale[1]],
    }
