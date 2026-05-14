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
def register():
    print("レベルエディタ有効化")


#

def unregister():
    print("レベルエディタ無効化")


if __name__ == "__main__":
    register()