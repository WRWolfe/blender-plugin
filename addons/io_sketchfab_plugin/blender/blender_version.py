import bpy

class Version:
    """Adjusts functions according to the differences between 2.79 and 2.8"""

    #Render engine
    ENGINE = "CYCLES" if bpy.app.version == (2, 79, 0) else "BLENDER_EEVEE"

    # Selection / Deselection
    def select(obj):
        if bpy.app.version == (2, 79, 0):
            obj.select = True
        elif bpy.app.version == (2, 80, 0):
            obj.select_set(True)
    def deselect(obj):
        if bpy.app.version == (2, 79, 0):
            obj.select = False
        elif bpy.app.version == (2, 80, 0):
            obj.select_set(False)

    # Object linking
    def link(scene, obj):
        if bpy.app.version == (2, 79, 0):
            bpy.data.scenes[scene].objects.link(obj)
        elif bpy.app.version == (2, 80, 0):
            bpy.data.scenes[scene].collection.objects.link(obj)

    # Active object
    def get_active_object():
        if bpy.app.version == (2, 79, 0):
            return bpy.context.scene.objects.active
        elif bpy.app.version == (2, 80, 0):
            return bpy.context.view_layer.objects.active
    def set_active_object(obj):
        if bpy.app.version == (2, 79, 0):
            bpy.context.scene.objects.active = obj
        elif bpy.app.version == (2, 80, 0):
            bpy.context.view_layer.objects.active = obj

    # Matrix multiplication
    def mat_mult(A, B):
        if bpy.app.version == (2, 79, 0):
            return A * B
        elif bpy.app.version == (2, 80, 0):
            return A @ B

    # Setting colorspace
    def set_colorspace(texture):
        if bpy.app.version == (2, 79, 0):
            texture.color_space = 'NONE'
        elif bpy.app.version == (2, 80, 0):
            if texture.image:
                texture.image.colorspace_settings.is_data = True
