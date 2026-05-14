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
        
    def submenu(self, context):
        self.layout.menu(TOPBAR_MT_my_menu.bl_idname)

classes = (
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