import bpy
import math
import os
import csv

bl_info = {
    "name": "レベルエディタ (AI対応・動的配置版)",
    "author": "Sota Sugiyama",
    "version": (1,1),
    "blender": (4, 0, 0), # 互換性を考慮して調整しています
    "location": "トップバー > MyMenu",
    "description": "2Dアクションゲーム用のグリッド配置・エクスポートツール",
    "warning": "",
    "category": "3D View",
}

# --- 1. CSVマップを読み込んで配置するオペレーター ---
# --- 1. CSVマップを読み込んで配置するオペレーター ---
class MYADDON_OT_import_csv_map(bpy.types.Operator):
    bl_idname = "myaddon.import_csv_map"
    bl_label  = "CSVマップ読み込み"
    bl_description = "古い配置を消去し、CSVファイルを読み込んで2Dマップを自動配置します"
    bl_options = { 'REGISTER', 'UNDO' }

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.csv", options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not os.path.exists(self.filepath):
            self.report({'ERROR'}, "ファイルが見つかりません")
            return {'CANCELLED'}

        # ==========================================
        # ⭐【追加機能】すでに配置されている古いオブジェクトを消去
        # ==========================================
        # 名前の後ろに "_Placed" がつくオブジェクトをすべて探して削除する
        objects_to_delete = [obj for obj in bpy.context.scene.objects if obj.name.endswith("_Placed")]
        
        # 削除処理を実行
        for obj in objects_to_delete:
            bpy.data.objects.remove(obj, do_unlink=True)
            
        print(f"古いオブジェクトを {len(objects_to_delete)} 個消去しました")
        # ==========================================

        # 元となるオブジェクトがシーンにあるか確認（なければ簡易作成）
        if "Wall" not in bpy.data.objects:
            bpy.ops.mesh.primitive_cube_add(size=1)
            wall = bpy.context.active_object
            wall.name = "Wall"
            
        if "Enemy_Zombie" not in bpy.data.objects:
            bpy.ops.mesh.primitive_ico_sphere_add(radius=0.5)
            enemy = bpy.context.active_object
            enemy.name = "Enemy_Zombie"

        # CSVの読み込み・配置処理
        with open(self.filepath, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            for y, row in enumerate(reader):
                for x, val in enumerate(row):
                    pos_x = x
                    pos_z = -y 

                    if val == "1": # 壁
                        obj_data = bpy.data.objects["Wall"].data
                        # 名前の後ろに "_Placed" をつけて、次回消去の対象にする
                        new_obj = bpy.data.objects.new("Wall_Placed", obj_data)
                        bpy.context.collection.objects.link(new_obj)
                        new_obj.location = (pos_x, 0, pos_z)
                        new_obj["type"] = "Wall"

                    elif val == "E": # 敵
                        obj_data = bpy.data.objects["Enemy_Zombie"].data
                        # 名前の後ろに "_Placed" をつけて、次回消去の対象にする
                        new_obj = bpy.data.objects.new("Enemy_Placed", obj_data)
                        bpy.context.collection.objects.link(new_obj)
                        new_obj.location = (pos_x, 0, pos_z)
                        
                        new_obj["type"] = "Enemy"
                        new_obj["spawn_range"] = 5.0
                        new_obj["move_speed"] = 2.0

        self.report({'INFO'}, "古い配置をクリアし、CSVマップを配置しました")
        return {'FINISHED'}


# --- 2. 既存の機能（頂点を伸ばす・ICO球作成） ---
class MYADDON_OT_stretch_vertex(bpy.types.Operator):
    bl_idname = "myaddon.myaddon_ot_stretch_vertex"
    bl_label  = "焦点を伸ばす"
    bl_options = { 'REGISTER', 'UNDO' }

    def execute(self, context):
        if "Cube" in bpy.data.objects:
            bpy.data.objects["Cube"].data.vertices[0].co.x += 1.0
            print("頂点を伸ばしました")
        return {'FINISHED'}

class MYADDON_OT_create_ico_sphere(bpy.types.Operator):
    bl_idname = "myaddon.myaddon_ot_create_object"
    bl_label  = "ICO球作成"
    bl_options = { 'REGISTER', 'UNDO' }

    def execute(self, context):
        bpy.ops.mesh.primitive_ico_sphere_add()
        return {'FINISHED'}


# --- 3. 拡張されたエクスポート（カスタムプロパティも出力） ---
class MYADDON_OT_export_scene(bpy.types.Operator):
    bl_idname = "myaddon.myaddon_ot_export_scene"
    bl_label  = "シーン出力"

    def execute(self, context):
        print("====== シーン情報Export開始 ======")
        for obj in bpy.context.scene.objects:
            # 元データ（テンプレート）はスキップし、配置された物だけ出力
            if obj.name in ["Wall", "Enemy_Zombie"]:
                continue

            print(f"Object: {obj.name}")
            
            # 座標データの取得
            trans, rot, scale = obj.matrix_local.decompose()
            rot = rot.to_euler()
            
            print("  Trans(%f, %f, %f)" % (trans.x, trans.y, trans.z))
            
            # ⭐カスタムプロパティ（出現条件など）があれば一緒に出力する
            if "type" in obj:
                print(f"  Type: {obj['type']}")
            if "spawn_range" in obj:
                print(f"  Spawn Range: {obj['spawn_range']}") # ここがUnityやGodotに渡る情報になる
            if "move_speed" in obj:
                print(f"  Move Speed: {obj['move_speed']}")
                
            print("-" * 20)

        print("====== シーン情報Export完了 ======")
        self.report({'INFO'}, "コンソールに出力しました（カスタムプロパティ含む）")
        return {'FINISHED'}


# --- 4. メニュー表示の登録 ---
class TOPBAR_MT_my_menu(bpy.types.Menu):
    bl_idname = "TOPBAR_MT_my_menu"
    bl_label = "世界演出エディタ" 
    bl_description = "拡張メニュー by " + bl_info["author"]

    def draw(self, context):
        layout = self.layout
        layout.operator("wm.url_open_preset", text="Manual", icon='HELP')
        layout.separator()
        
        # 追加したCSV読み込み機能
        layout.operator(MYADDON_OT_import_csv_map.bl_idname, text=MYADDON_OT_import_csv_map.bl_label, icon='FILE_BLANK')
        layout.separator()
        
        layout.operator(MYADDON_OT_stretch_vertex.bl_idname, text=MYADDON_OT_stretch_vertex.bl_label)
        layout.operator(MYADDON_OT_create_ico_sphere.bl_idname, text=MYADDON_OT_create_ico_sphere.bl_label)
        layout.operator(MYADDON_OT_export_scene.bl_idname, text=MYADDON_OT_export_scene.bl_label, icon='EXPORT')
        
    def submenu(self, context):
        self.layout.menu(TOPBAR_MT_my_menu.bl_idname)

classes = (
    MYADDON_OT_import_csv_map,
    MYADDON_OT_stretch_vertex,
    MYADDON_OT_create_ico_sphere,
    MYADDON_OT_export_scene,
    TOPBAR_MT_my_menu,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_editor_menus.append(TOPBAR_MT_my_menu.submenu)
    print("レベルエディタ有効化")

def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)
    bpy.types.TOPBAR_MT_editor_menus.remove(TOPBAR_MT_my_menu.submenu)
    print("レベルエディタ無効化")

if __name__ == "__main__":
    register()