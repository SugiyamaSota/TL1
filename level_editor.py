import bpy
import math
import bpy_extras

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

class MYADDON_OT_export_scene(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "myaddon.myaddon_ot_export_scene"
    bl_label  = "シーン出力"
    dl_description = "シーン情報をexportします"
     
    # 出力するファイルの拡張子
    filename_ext = ".scene" 

    # コンソール出力とファイル出力を同時に行う関数
    def write_and_print(self, file, str):
        print(str)

        file.write(str)
        file.write("\n")

    def parse_scene_recursive(self, file, object, level):
        """シーン情報を再帰的に出力"""

        # インデント
        indent = ''
        for i in range(level):
            indent += '\t'
        
        # オブジェクト名書き込み
        self.write_and_print(file, indent + object.type + " - " + object.name)
        trans, rot, scale = object.matrix_local.decompose()
        #回転を Quaternion から　Euler へ変換
        rot = rot.to_euler()
        #ラジアンから度数法に変換
        rot.x = math.degrees(rot.x)
        rot.y = math.degrees(rot.y)
        rot.z = math.degrees(rot.z)
        #トランスフォーム情報書き込み
        self.write_and_print(file, indent + "Trans(%f,%f,%f)" % (trans.x, trans.y, trans.z))
        self.write_and_print(file, indent + "Rot(%f,%f,%f)" % (rot.x, rot.y, rot.z))
        self.write_and_print(file, indent + "Scale(%f,%f,%f)" % (scale.x, scale.y, scale.z))
        self.write_and_print(file, "")

        for child in object.children:
            self.parse_scene_recursive(file, child, level + 1)

    def export(self):
        """ファイルに出力"""
        print("シーン情報出力開始... %r" % self.filepath)
     
        # ファイルをテキスト形式で書き出し用にオープン
        with open(self.filepath, "wt") as file:
            file.write("SCENE\n")

            for object in bpy.context.scene.objects:
                

                if object.parent:
                    continue
                self.parse_scene_recursive(file, object, 0)

    def execute(self, context):
        print("シーン情報をExport")

        # ファイル出力関数を実行
        self.export()

        self.report({'INFO'}, "シーン情報をExport済")
        print("シーン情報Export済")

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
        self.layout.operator(MYADDON_OT_export_scene.bl_idname,
                             text = MYADDON_OT_export_scene.bl_label)
        
    def submenu(self, context):
        self.layout.menu(TOPBAR_MT_my_menu.bl_idname)

classes = (
    MYADDON_OT_stretch_vertex,
    MYADDON_OT_create_ico_sphere,
    MYADDON_OT_export_scene,

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