import bpy

#

bl_info = {
    "name": "レベルエディタ",
    "author": "Sota Sugiyama",
    "version": (1,0),
    "blender": (5, 1, 1),
    "location": "",
    "description":"レベルエディタ",
    "warning": "",
    "wiki_url":"",
    "tracker_url":"",
    "category": "3D View",
}

# オペレータークラス
# Cubeの頂点を伸ばす
class MYADDON_OT_stretch_vertex(bpy.types.Operator):
    bl_idname = "myaddon.myaddon_ot_stretch_vertex"
    bl_label  = "焦点を伸ばす"
    dl_description = "頂点座標を引っ張って伸ばします"
    # Redo,Undo
    bl_options = { 'REGISTER', 'UNDO' }

    def execute(self, context):
        bpy.data.objects["Cube"].data.vertices[0].co.x +=1.0
        print("頂点を伸ばしました")

        #
        return {'FINISHED'}

# ICO球
class MYADDON_OT_create_ico_sphere(bpy.types.Operator):
    bl_idname = "myaddon.myaddon_ot_create_object"
    bl_label  = "ICO球作成"
    dl_description = "ICO球を作成します"
    # Redo,Undo
    bl_options = { 'REGISTER', 'UNDO' }

    def execute(self, context):
        bpy.ops.mesh.primitive_ico_sphere_add()
        print("ICO球を作成しました")

        #
        return {'FINISHED'}

#
class TOPBAR_MT_my_menu(bpy.types.Menu):
    #
    bl_idname = "TOPBAR_MT_my_menu"
    #
    bl_label = "MyMenu" 
    #
    bl_description = "拡張メニュー by " + bl_info["author"]

    # 有効化時処理追加しろ
    def draw(self, context):
        self.layout.operator("wm.url_open_preset",
                             text="Manual",icon='HELP')
        self.layout.operator(MYADDON_OT_stretch_vertex.bl_idname,
                             text = MYADDON_OT_stretch_vertex.bl_label)
        self.layout.operator(MYADDON_OT_create_ico_sphere.bl_idname,
                             text = MYADDON_OT_create_ico_sphere.bl_label)
        
    def submenu(self, context):
        self.layout.menu(TOPBAR_MT_my_menu.bl_idname)

classes = (
    MYADDON_OT_stretch_vertex,
    MYADDON_OT_create_ico_sphere,
    TOPBAR_MT_my_menu,
)

#
def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy .types.TOPBAR_MT_editor_menus.append(TOPBAR_MT_my_menu.submenu)
       

    print("レベルエディタ有効化")


#

def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)

        bpy .types.TOPBAR_MT_editor_menus.remove(TOPBAR_MT_my_menu.submenu)

    print("レベルエディタ無効化")


if __name__ == "__main__":
    register()