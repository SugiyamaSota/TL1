import bpy
import math

import os
import csv
import io
import json
import threading
import time
import urllib.error
import urllib.request
import mathutils
import bpy_extras
import gpu
import gpu_extras.batch
import copy
import json

bl_info = {
    "name": "レベルエディタ (AI対応・動的配置版)",
    "author": "Sota Sugiyama",
    "version": (1,2),
    "blender": (4, 0, 0),
    "location": "トップバー > MyMenu",
    "description": "2Dアクションゲーム用のグリッド配置・エクスポートツール (AI対応版)",
    "warning": "",
    "category": "3D View",
}

SYSTEM_PROMPT = """You are an expert game level designer.
Generate an exciting and playable 2D side-scrolling game map as a CSV grid of the requested size.

[CRITICAL RULE: EXACT SIZE REQUIRED]
- You MUST generate the EXACT size requested (width and height). Do not try to make it wider or taller.
- If the requested size is 20 columns wide and 20 rows high, you must output EXACTLY 20 rows, and each row must contain EXACTLY 20 values (separated by exactly 19 commas).
- Any deviation in columns or rows will CRASH the parser. Count columns and rows carefully.

[CRITICAL RULE: PLAYABLE DESIGN]
- Do NOT generate a mostly empty map. It must feel like a real game level.
- You must build interesting features like a solid ground at the bottom (e.g., the bottom 1 or 2 rows should be filled with '1's), floating platforms/ledges ('1's) in the air, and place enemies ('E') on top of them.
- Use '0' only for empty air spaces.

[CRITICAL RULE: OUTPUT FORMAT]
- You MUST output the CSV data inside a markdown code block starting with ```csv and ending with ```.
- Do NOT include any explanations, raw talk, or preamble outside the code block.
"""

RETRYABLE_HTTP_CODES = {500, 503, 504, 429}


class APIRequestError(Exception):
    def __init__(self, code, detail):
        self.code = code
        self.detail = detail
        super().__init__(f"APIエラー {code}: {detail[:300]}")


# --- 1. マッピング用のデータ定義とPreferences ---

class AICSVMapMappingItem(bpy.types.PropertyGroup):
    symbol: bpy.props.StringProperty(name="記号", default="")
    object_name: bpy.props.StringProperty(name="オブジェクト名", default="")


class LevelEditorPreferences(bpy.types.AddonPreferences):
    bl_idname = "level_editor"

    provider: bpy.props.EnumProperty(
        name="AIサービス",
        items=(
            ("GEMINI", "Google Gemini", "Google AI StudioのGemini APIを使用"),
            ("OPENAI", "OpenAI互換", "OpenAI互換Chat Completions APIを使用"),
        ),
        default="GEMINI",
    )
    api_key: bpy.props.StringProperty(
        name="API Key",
        description="空欄の場合は選択したサービスの環境変数または ai_csv_generator アドオンの設定値を使用します",
        subtype="PASSWORD",
    )
    endpoint: bpy.props.StringProperty(
        name="API URL",
        default="https://api.openai.com/v1/chat/completions",
    )
    model: bpy.props.StringProperty(
        name="Model",
        default="gemini-3.5-flash",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "provider")
        layout.prop(self, "api_key")
        layout.prop(self, "model")
        if self.provider == "OPENAI":
            layout.prop(self, "endpoint")
            layout.label(text="環境変数 OPENAI_API_KEY も使用できます")
        else:
            layout.label(text="環境変数 GEMINI_API_KEY / GOOGLE_API_KEY も使用できます")


def addon_preferences(context):
    addon = context.preferences.addons.get("level_editor")
    if not addon:
        addon = context.preferences.addons.get(__name__)
    return addon.preferences if addon else None


# --- 2. API連携のヘルパー関数群 ---

def get_provider(context):
    prefs = addon_preferences(context)
    if prefs:
        return prefs.provider
    old_addon = context.preferences.addons.get("ai_csv_generator")
    if old_addon and old_addon.preferences:
        return getattr(old_addon.preferences, "provider", "GEMINI")
    return "GEMINI"


def get_api_key(context, provider):
    prefs = addon_preferences(context)
    api_key = ""
    if prefs:
        api_key = prefs.api_key.strip()
    
    if not api_key:
        if provider == "GEMINI":
            api_key = (
                os.environ.get("GOOGLE_API_KEY", "").strip()
                or os.environ.get("GEMINI_API_KEY", "").strip()
            )
        else:
            api_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if not api_key:
        old_addon = context.preferences.addons.get("ai_csv_generator")
        if old_addon and old_addon.preferences:
            api_key = getattr(old_addon.preferences, "api_key", "").strip()

    return api_key


def get_model(context, provider):
    prefs = addon_preferences(context)
    if prefs and prefs.model.strip():
        model = prefs.model.strip()
    else:
        old_addon = context.preferences.addons.get("ai_csv_generator")
        if old_addon and old_addon.preferences and getattr(old_addon.preferences, "model", "").strip():
            model = old_addon.preferences.model.strip()
        else:
            model = "gemini-3.5-flash"
    
    if provider == "GEMINI" and model.startswith("gpt-"):
        model = "gemini-3.5-flash"
    elif provider == "OPENAI" and model.startswith("gemini-"):
        model = "gpt-4o-mini"
    return model


def get_endpoint(context):
    prefs = addon_preferences(context)
    if prefs and prefs.endpoint.strip():
        return prefs.endpoint.strip()
    old_addon = context.preferences.addons.get("ai_csv_generator")
    if old_addon and old_addon.preferences and getattr(old_addon.preferences, "endpoint", "").strip():
        return old_addon.preferences.endpoint.strip()
    return "https://api.openai.com/v1/chat/completions"


def clean_csv_response(text, width, height, empty_char="0"):
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines.pop(0)
        if lines and lines[-1].strip() == "```":
            lines.pop()
        text = "\n".join(lines).strip()

    # 2. カンマを一定数以上含む行のみをフィルタリングして残す（雑談や思考プロセスの行を除外）
    min_commas = max(3, width // 2)
    lines = text.splitlines()
    csv_lines = [line.strip() for line in lines if line.count(",") >= min_commas]
    
    if csv_lines:
        text = "\n".join(csv_lines)

    # CSVのパース
    rows = list(csv.reader(io.StringIO(text)))
    
    # セル値を持つ有効な行のみを抽出
    rows = [row for row in rows if row and any(cell.strip() for cell in row)]
    
    if not rows:
        # 全くデータがない場合はすべて空白のグリッドを作成
        rows = [[empty_char] * width for _ in range(height)]
    
    
    normalized_rows = []
    for row in rows:
        clean_row = [cell.strip() for cell in row]
        if len(clean_row) < width:
            # 不足している列を空白記号で埋める
            clean_row.extend([empty_char] * (width - len(clean_row)))
        elif len(clean_row) > width:
            # 超過している列を切り捨てる
            clean_row = clean_row[:width]
        normalized_rows.append(clean_row)
        
    # 行数が足りない場合は追加
    if len(normalized_rows) < height:
        for _ in range(height - len(normalized_rows)):
            normalized_rows.append([empty_char] * width)
    # 行数が多い場合は切り捨てる
    elif len(normalized_rows) > height:
        normalized_rows = normalized_rows[:height]

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerows(normalized_rows)
    return output.getvalue()


def request_gemini_once(api_key, model, prompt):
    # APIキーはURLのパラメータとして渡します
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = {
        "systemInstruction": {
            "parts": [{"text": SYSTEM_PROMPT}],
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 8192,
        },
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
            parts = result["candidates"][0]["content"]["parts"]
            return "".join(part.get("text", "") for part in parts)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise APIRequestError(error.code, detail) from error


def request_openai_once(api_key, endpoint, model, prompt):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 8192,
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise APIRequestError(error.code, detail) from error


def request_with_retry(provider, api_key, endpoint, model, prompt, update_status):
    models = [model]
    if provider == "GEMINI" and model != "gemini-2.5-flash":
        models.append("gemini-2.5-flash")

    last_error = None
    for current_model in models:
        for attempt in range(3):
            try:
                update_status(f"{current_model} に送信中（{attempt + 1}/3）")
                if provider == "GEMINI":
                    return request_gemini_once(api_key, current_model, prompt)
                return request_openai_once(api_key, endpoint, current_model, prompt)
            except APIRequestError as error:
                last_error = error
                if error.code not in RETRYABLE_HTTP_CODES:
                    raise
                if attempt < 2:
                    wait_seconds = 2 ** (attempt + 1)
                    if error.code == 429:
                        wait_seconds = 20
                        update_status("クォータ制限(429)のため20秒待機して再試行します")
                    else:
                        update_status(
                            f"APIが混雑中です。{wait_seconds}秒後に再試行します"
                        )
                    time.sleep(wait_seconds)

        if len(models) > 1 and current_model != models[-1]:
            update_status("軽量モデル gemini-2.5-flash に切り替えます")

    raise last_error


# --- 3. マッピング操作用オペレーター群 ---

class AICSV_OT_mapping_add(bpy.types.Operator):
    bl_idname = "ai_csv.mapping_add"
    bl_label = "マッピング追加"
    bl_description = "CSVの記号とシーンのオブジェクトの新しい対応を追加します"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        context.scene.ai_csv_mappings.add()
        return {"FINISHED"}


class AICSV_OT_mapping_remove(bpy.types.Operator):
    bl_idname = "ai_csv.mapping_remove"
    bl_label = "マッピング削除"
    bl_description = "この対応設定を削除します"
    bl_options = {"REGISTER", "UNDO"}

    index: bpy.props.IntProperty()

    def execute(self, context):
        mappings = context.scene.ai_csv_mappings
        if 0 <= self.index < len(mappings):
            mappings.remove(self.index)
        return {"FINISHED"}


# --- 4. AIによるCSV生成オペレーター ---

class AICSV_OT_generate(bpy.types.Operator):
    bl_idname = "ai_csv.generate"
    bl_label = "AIでCSVを生成"
    bl_description = "プロンプトをAIへ送り、返答をCSVとして保存します"
    bl_options = {"REGISTER"}

    _timer = None
    _thread = None
    _result = None

    def invoke(self, context, event):
        scene = context.scene
        provider = get_provider(context)
        api_key = get_api_key(context, provider)
        if not api_key:
            self.report({"ERROR"}, "アドオン設定または環境変数でAPIキーを入力してください")
            return {"CANCELLED"}

        user_prompt = scene.ai_csv_prompt.strip()
        if not user_prompt:
            self.report({"ERROR"}, "プロンプトを入力してください")
            return {"CANCELLED"}

        width = scene.ai_csv_map_width
        height = scene.ai_csv_map_height
        empty = scene.ai_csv_char_empty.strip() or "0"

        mappings_desc = []
        for item in scene.ai_csv_mappings:
            sym = item.symbol.strip()
            obj = item.object_name.strip()
            if sym and obj:
                mappings_desc.append(f"{obj}が{sym}")

        if mappings_desc:
            mappings_desc.append(f"空白が{empty}")
        else:
            wall = scene.ai_csv_char_wall.strip() or "1"
            enemy = scene.ai_csv_char_enemy.strip() or "E"
            mappings_desc = [f"ブロックが{wall}", f"敵が{enemy}", f"空白が{empty}"]

        mappings_str = "、".join(mappings_desc)
        prefix = (
            f"Create a 2D game map CSV of size: EXACTLY {width} columns wide (width) and EXACTLY {height} rows high (height).\n"
            f"Do not write any header row. Output exactly {height} rows of data. Each row must contain exactly {width} values separated by commas.\n"
            f"Legend/Representations: {mappings_str}.\n"
            f"（幅 {width} 列、高さ {height} 行の2Dゲームマップを表すCSVを作成してください。ヘッダー行は絶対に含めないでください。各行にはちょうど {width} 個の値をカンマ区切りで記述し、全体でちょうど {height} 行となるように出力してください。記号の対応：{mappings_str}）"
        )
        prompt = f"{prefix}\n{user_prompt}"

        model = get_model(context, provider)
        endpoint = get_endpoint(context)

        output_path = bpy.path.abspath(scene.ai_csv_output_path)
        if not output_path:
            self.report({"ERROR"}, "保存先を指定してください")
            return {"CANCELLED"}
        output_path = os.path.normpath(output_path)

        self._result = {"done": False, "status": "AIへ送信する準備中"}
        scene.ai_csv_status = self._result["status"]
        scene.ai_csv_is_running = True

        def update_status(message):
            self._result["status"] = message

        def worker():
            try:
                content = request_with_retry(
                    provider,
                    api_key,
                    endpoint,
                    model,
                    prompt,
                    update_status,
                )
                csv_text = clean_csv_response(content, width, height, empty)
                parent = os.path.dirname(output_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(output_path, "w", encoding="utf-8-sig", newline="") as file:
                    file.write(csv_text)
                self._result.update(
                    done=True,
                    csv_text=csv_text,
                    output_path=output_path,
                )
            except APIRequestError as error:
                if error.code == 503:
                    message = (
                        "Geminiが混雑しており、再試行しても応答できませんでした。"
                        "少し時間を置いて再実行してください"
                    )
                else:
                    message = f"APIエラー {error.code}: {error.detail[:300]}"
                self._result.update(done=True, error=message)
            except urllib.error.URLError as error:
                self._result.update(done=True, error=f"通信エラー: {error.reason}")
            except (KeyError, IndexError, TypeError, json.JSONDecodeError):
                self._result.update(
                    done=True,
                    error="APIの返答形式を読み取れませんでした",
                )
            except (OSError, ValueError) as error:
                self._result.update(done=True, error=str(error))
            except Exception as error:
                self._result.update(done=True, error=f"予期しないエラー: {error}")

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()
        self._timer = context.window_manager.event_timer_add(
            0.25,
            window=context.window,
        )
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}

        context.scene.ai_csv_status = self._result.get("status", "処理中")
        for area in context.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()

        if not self._result.get("done"):
            return {"RUNNING_MODAL"}

        context.window_manager.event_timer_remove(self._timer)
        self._timer = None
        context.scene.ai_csv_is_running = False

        if "error" in self._result:
            context.scene.ai_csv_status = self._result["error"]
            self.report({"ERROR"}, self._result["error"])
            return {"CANCELLED"}

        context.scene.ai_csv_last_result = self._result["csv_text"]
        context.scene.ai_csv_status = "CSVを保存しました"
        self.report(
            {"INFO"},
            f"CSVを保存しました: {self._result['output_path']}",
        )
        return {"FINISHED"}


# --- 5. CSVマップを読み込んで配置するオペレーター群 ---

class AICSV_OT_import(bpy.types.Operator):
    bl_idname = "ai_csv.import_map"
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

        # 古いオブジェクトの消去
        objects_to_delete = [obj for obj in bpy.context.scene.objects if obj.name.endswith("_Placed")]
        for obj in objects_to_delete:
            bpy.data.objects.remove(obj, do_unlink=True)
            
        print(f"古いオブジェクトを {len(objects_to_delete)} 個消去しました")

        scene = context.scene

        # シンボルとオブジェクト名のマッピングを構築
        symbol_to_object = {}
        for item in scene.ai_csv_mappings:
            sym = item.symbol.strip()
            obj = item.object_name.strip()
            if sym and obj:
                symbol_to_object[sym] = obj

        if not symbol_to_object:
            char_wall = scene.ai_csv_char_wall.strip() or "1"
            char_enemy = scene.ai_csv_char_enemy.strip() or "E"
            symbol_to_object[char_wall] = "Wall"
            symbol_to_object[char_enemy] = "Enemy"

        # CSV読み込みと配置
        with open(self.filepath, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            for y, row in enumerate(reader):
                for x, val in enumerate(row):
                    pos_x = x
                    pos_z = -y 

                    val_clean = val.strip()
                    if val_clean in symbol_to_object:
                        target_obj_name = symbol_to_object[val_clean]

                        # オブジェクトが存在しない場合の簡易作成
                        if target_obj_name not in bpy.data.objects:
                            if target_obj_name == "Wall":
                                bpy.ops.mesh.primitive_cube_add(size=1)
                                wall = bpy.context.active_object
                                wall.name = "Wall"
                            elif target_obj_name == "Enemy":
                                bpy.ops.mesh.primitive_ico_sphere_add(radius=0.5)
                                enemy = bpy.context.active_object
                                enemy.name = "Enemy"
                            else:
                                bpy.ops.mesh.primitive_cube_add(size=1)
                                placeholder = bpy.context.active_object
                                placeholder.name = target_obj_name

                        obj_template = bpy.data.objects[target_obj_name]
                        obj_data = obj_template.data

                        new_obj = bpy.data.objects.new(f"{target_obj_name}_Placed", obj_data)
                        bpy.context.collection.objects.link(new_obj)
                        new_obj.location = (pos_x, 0, pos_z)

                        # テンプレートのスケール、回転、カスタムプロパティを適用
                        new_obj.scale = obj_template.scale.copy()
                        new_obj.rotation_euler = obj_template.rotation_euler.copy()

                        for key, value in obj_template.items():
                            if key not in ["_RNA_UI", "cycles"]:
                                new_obj[key] = value

                        new_obj["type"] = target_obj_name

                        if target_obj_name == "Enemy":
                            new_obj["spawn_range"] = 5.0
                            new_obj["move_speed"] = 2.0

        self.report({'INFO'}, "古い配置をクリアし、CSVマップを配置しました")
        return {'FINISHED'}


# 旧オペレーター（後方互換性維持用）
class MYADDON_OT_import_csv_map(bpy.types.Operator):
    bl_idname = "myaddon.import_csv_map"
    bl_label  = "CSVマップ読み込み"
    bl_description = "古い配置を消去し、CSVファイルを読み込んで2Dマップを自動配置します"
    bl_options = { 'REGISTER', 'UNDO' }

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.csv", options={'HIDDEN'})

    def invoke(self, context, event):
        bpy.ops.ai_csv.import_map('INVOKE_DEFAULT')
        return {'FINISHED'}

    def execute(self, context):
        return bpy.ops.ai_csv.import_map(filepath=self.filepath)


# --- 6. 既存の機能 (焦点を伸ばす・ICO球作成・エクスポート) ---

class MYADDON_OT_stretch_vertex(bpy.types.Operator):
    bl_idname = "myaddon.myaddon_ot_stretch_vertex"
    bl_label  = "焦点を伸ばす"
    bl_options = { 'REGISTER', 'UNDO' }

    def execute(self, context):
        if "Cube" in bpy.data.objects:
            bpy.data.objects["Cube"].data.vertices[0].co.x += 1.0
            print("頂点を伸ばしました")
        return {'FINISHED'}


#パネル　ファイル名
class OBJECT_PT_file_name(bpy.types.Panel):
    """オブジェクトのファイルネームパネル"""
    bl_idname = "OBJECT_PT_file_name"
    bl_label  = "FileName"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_category = "object"

    # サブメニューの描画
    def draw(self, context):
        self.layout.operator(MYADDON_OT_stretch_vertex.bl_idname,text = MYADDON_OT_stretch_vertex.bl_label)
        self.layout.operator(MYADDON_OT_create_ico_sphere.bl_idname,text = MYADDON_OT_create_ico_sphere.bl_label)
        self.layout.operator(MYADDON_OT_export_scene.bl_idname,text = MYADDON_OT_export_scene.bl_label)

        if "file_name" in context.object:
            self.layout.prop(context.object, '["file_name"]', text = self.bl_label)
        else:
            self.layout.operator(MYADDON_OT_add_filename.bl_idname)

#パネル コライダー
class OBJECT_PT_collider(bpy.types.Panel):
    bl_idname = "OBJECT_PT_collider"
    bl_label = "Collider"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    # サブメニューの描画
    def draw(self, context):
        #パネルに項目を追加
        if "collider" in context.object:
            #既にプロパティがあれば、プロパティを表示
            self.layout.prop(context.object, '["collider"]', text="Type")
            self.layout.prop(context.object, '["collider_center"]', text="Center")
            self.layout.prop(context.object, '["collider_size"]', text="Size")
        else:
            #プロパティがなければ、プロパティ追加ボタンを表示
            self.layout.operator(MYADDON_OT_add_collider.bl_idname)


#オペレーター　カスタムプロパティ
class MYADDON_OT_add_filename(bpy.types.Operator):
    bl_idname = "myaddon.myaddon_ot_add_filename"
    bl_label  = "FileName追加"
    dl_description = "['file_name']カスタムプロパティを追加します"
    bl_options = { 'REGISTER', 'UNDO' }

    def execute(self, context):
        #カスタムプロパティを追加
        context.object["file_name"] = ""

        #
        return {'FINISHED'}

# オペレータ カスタムプロパティ追加
class MYADDON_OT_add_collider(bpy.types.Operator):
    bl_idname = "myaddon.myaddon_ot_add_collider"
    bl_label = "コライダー 追加"
    bl_description = "['collider']カスタムプロパティを追加します"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # ['collider']カスタムプロパティを追加
        context.object["collider"] = "BOX"
        context.object["collider_center"] = mathutils.Vector((0,0,0))
        context.object["collider_size"] = mathutils.Vector((2,2,2))
        return {'FINISHED'}

# ICO球
class MYADDON_OT_create_ico_sphere(bpy.types.Operator):
    bl_idname = "myaddon.myaddon_ot_create_object"
    bl_label  = "ICO球作成"
    bl_options = { 'REGISTER', 'UNDO' }

    def execute(self, context):
        bpy.ops.mesh.primitive_ico_sphere_add()
        return {'FINISHED'}

class MYADDON_OT_export_scene(bpy.types.Operator):
    bl_idname = "myaddon.myaddon_ot_export_scene"
    bl_label  = "シーン出力"

    def execute(self, context):
        print("====== シーン情報Export開始 ======")
        for obj in bpy.context.scene.objects:
            if obj.name in ["Wall", "Enemy"]:
                continue

            print(f"Object: {obj.name}")
            trans, rot, scale = obj.matrix_local.decompose()
            rot = rot.to_euler()
            print("  Trans(%f, %f, %f)" % (trans.x, trans.y, trans.z))
            
            if "type" in obj:
                print(f"  Type: {obj['type']}")
            if "spawn_range" in obj:
                print(f"  Spawn Range: {obj['spawn_range']}")
            if "move_speed" in obj:
                print(f"  Move Speed: {obj['move_speed']}")
            print("-" * 20)

        print("====== シーン情報Export完了 ======")
        self.report({'INFO'}, "コンソールに出力しました")
        return {'FINISHED'}


# --- 7. UI Sidebar Panel & Topbar Menu ---

class AICSV_PT_panel(bpy.types.Panel):
    bl_label = "AI CSV Generator"
    bl_idname = "AICSV_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AI CSV"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        box_cfg = layout.box()
        box_cfg.label(text="マップ設定", icon="SCENE_DATA")
        
        row_size = box_cfg.row(align=True)
        row_size.prop(scene, "ai_csv_map_width", text="幅")
        row_size.prop(scene, "ai_csv_map_height", text="高さ")
        
        box_cfg.separator()
        box_cfg.label(text="記号とオブジェクトの対応設定:")
        
        for i, item in enumerate(scene.ai_csv_mappings):
            row = box_cfg.row(align=True)
            row.prop(item, "symbol", text="記号")
            row.prop(item, "object_name", text="オブジェクト")
            op = row.operator("ai_csv.mapping_remove", icon="REMOVE", text="")
            op.index = i
            
        box_cfg.operator("ai_csv.mapping_add", icon="ADD", text="対応を追加")
        box_cfg.prop(scene, "ai_csv_char_empty", text="空白の記号")

        if not scene.ai_csv_mappings:
            box_cfg.separator()
            box_cfg.label(text="デフォルト (対応が空の場合):", icon="INFO")
            col_symbols = box_cfg.column(align=True)
            col_symbols.prop(scene, "ai_csv_char_wall", text="壁ブロック")
            col_symbols.prop(scene, "ai_csv_char_enemy", text="敵")

        layout.separator()
        layout.label(text="作りたい表を入力")
        layout.prop(scene, "ai_csv_prompt", text="")
        layout.prop(scene, "ai_csv_output_path")
        row = layout.row()
        row.enabled = not scene.ai_csv_is_running
        row.operator("ai_csv.generate", icon="FILE_TICK")

        if scene.ai_csv_status:
            box = layout.box()
            box.label(
                text=scene.ai_csv_status[:100],
                icon="TIME" if scene.ai_csv_is_running else "INFO",
            )

        if scene.ai_csv_last_result:
            box = layout.box()
            box.label(text="前回の生成結果")
            for line in scene.ai_csv_last_result.splitlines()[:8]:
                box.label(text=line[:100])

        layout.separator()
        layout.label(text="マップ配置・編集")
        
        row_import = layout.row(align=True)
        row_import.operator("ai_csv.import_map", text="CSV選択...", icon="FILE_BLANK")
        op_immediate = row_import.operator("ai_csv.import_map", text="生成したCSVを即座に配置", icon="PLAY")
        op_immediate.filepath = scene.ai_csv_output_path

class MYADDON_OT_export_scene(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "myaddon.myaddon_ot_export_scene"
    bl_label  = "シーン出力"
    dl_description = "シーン情報をexportします"
     
    # 出力するファイルの拡張子
    filename_ext = ".json" 

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
        self.write_and_print(file, indent + object.type)
        trans, rot, scale = object.matrix_local.decompose()
        #回転を Quaternion から　Euler へ変換
        rot = rot.to_euler()
        #ラジアンから度数法に変換
        rot.x = math.degrees(rot.x)
        rot.y = math.degrees(rot.y)
        rot.z = math.degrees(rot.z)
        #トランスフォーム情報書き込み
        self.write_and_print(file, indent + "T(%f,%f,%f)" % (trans.x, trans.y, trans.z))
        self.write_and_print(file, indent + "R(%f,%f,%f)" % (rot.x, rot.y, rot.z))
        self.write_and_print(file, indent + "S(%f,%f,%f)" % (scale.x, scale.y, scale.z))
        # カスタムプロパティ'file_name'
        if "file_name" in object:
            self.write_and_print(file, indent + "N %s" % object["file_name"])

        # カスタムプロパティ'collision'
        if "collider" in object:
            self.write_and_print(file, indent + "C %s" % object["collider"])
            
            # コライダー中心点
            center = object["collider_center"]
            self.write_and_print(file, indent + "CC %f %f %f" % (center[0], center[1], center[2]))
            
            # コライダーサイズ
            size = object["collider_size"]
            self.write_and_print(file, indent + "CS %f %f %f" % (size[0], size[1], size[2]))
        self.write_and_print(file, indent + 'END')
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

    def parse_scene_recursive_json(self,data_parent,object,level):
         #シーンのオブジェクト1個分のjsonオブジェクト
        json_object=dict()
        #オジェクトの種類
        json_object["type"] = object.type
        #オブジェクト名
        json_object["name"] = object.name
        
        #その他の情報をパック
        trans,rot,scale=object.matrix_local.decompose()

        rot = rot.to_euler()

        rot.x = math.degrees(rot.x)
        rot.y = math.degrees(rot.y)
        rot.z = math.degrees(rot.z)

        transform = dict()
        transform["translation"] = (trans.x,trans.y,trans.z)
        transform["rotation"] =  (rot.x,rot.y,rot.z)
        transform["scale"] =  (scale.x,scale.y,scale.z)
        json_object["transform"] = transform

        if "file_name" in object:
            json_object["file_name"] = object["file_name"]

        if "collider" in object:
            collider = dict()
            collider["type"] = object["collider"]
            collider["center"] = list(object["collider_center"])
            collider["size"] = list(object["collider_size"])

            json_object["collider"] = collider
        
        #1個分のオブジェクトを親オブジェクトに登録
        data_parent.append(json_object)

        #子供のリストを走査
        if len(object.children) > 0:
            #子ノードリストを作成
            json_object["children"] = list()

            #子ノードへ進む
            for child in object.children:
                self.parse_scene_recursive_json(json_object["children"], child,level+1)

        
                    

    
    def export_json(self):
        """json形式でファイルに出力"""
        #保存する情報をまとめるdict
        json_object_root = dict()
        #ノード名
        json_object_root["name"] = "scene"
        #オブジェクトリストを作成
        json_object_root["objects"] = list()
        # scene内の全オブジェクトを走査してバック
        for object in bpy.context.scene.objects:
            if(object.parent):
             continue
      
            self.parse_scene_recursive_json(json_object_root["objects"], object,0)

        # オブジェクトをJSON文字列にエンコード
        json_text = json.dumps(json_object_root,ensure_ascii=False, cls=json.JSONEncoder,indent=4)
        #コンソールに描画
        print(json_text)

        # ファイルをテキスト形式で書きだすためにオープン
        # スコープを抜けると自動的にクローズ
        with open(self.filepath, "wt", encoding="utf-8") as file:
            #ファイルに文字列を書き込む
            file.write(json_text)

    def execute(self, context):
        print("シーン情報をExport")

        # ファイル出力関数を実行
        self.export()
        self.export_json()

        self.report({'INFO'}, "シーン情報をExport済")
        print("シーン情報Export済")

        return {'FINISHED'}
    
class DrawCollider:
    #コライダー描画
    handle = None

    #3Dビューに登録する描画関数
    def draw_collider():
       #頂点データ
       vertices = {"pos" : []}
       #インデックスデータ
       indices = []

       # 各頂点のオブジェクト中心からのオフセット
       offsets = [
           [-0.5, -0.5, -0.5],
           [+0.5, -0.5, -0.5],
           [-0.5, +0.5, -0.5],
           [+0.5, +0.5, -0.5],
           [-0.5, -0.5, +0.5],
           [+0.5, -0.5, +0.5],
           [-0.5, +0.5, +0.5],
           [+0.5, +0.5, +0.5],
       ]

       # 現在のシーンのオブジェクトリストを走査
       for object in bpy.context.scene.objects:

           # コライダープロパティがなければ、描画をスキップ
           if not "collider" in object:
               continue

           # 中心点、サイズの変数を宣言
           center = mathutils.Vector((0,0,0))
           size = mathutils.Vector((2,2,2))

           # プロパティから値を取得
           center[0]=object["collider_center"][0]
           center[1]=object["collider_center"][1]
           center[2]=object["collider_center"][2]
           size[0]=object["collider_size"][0]
           size[1]=object["collider_size"][1]
           size[2]=object["collider_size"][2]

           #追加前の頂点数           
           start = len(vertices["pos"])

           # 各頂点の座標を計算して頂点リストに追加
           for offset in offsets:
               
               pos = copy.copy(center) 
               #中心点からオフセット分ずらす
               pos[0]+=offset[0]*size[0]
               pos[1]+=offset[1]*size[1]
               pos[2]+=offset[2]*size[2]
               #ローカル座標からワールド座標に変換
               pos = object.matrix_world @ pos
               #頂点データリストに座標を追加
               vertices["pos"].append(pos)

           # 前面のインデックス
           indices.append([start + 0, start + 1])
           indices.append([start + 3, start + 3])
           indices.append([start + 0, start + 2])
           indices.append([start + 1, start + 3])
           # 奥面のインデックス
           indices.append([start + 4, start + 5])
           indices.append([start + 6, start + 7])
           indices.append([start + 4, start + 6])
           indices.append([start + 5, start + 7])
           # 側面のインデックス
           indices.append([start + 0, start + 4])
           indices.append([start + 1, start + 5])
           indices.append([start + 2, start + 6])
           indices.append([start + 3, start + 7])

       # ビルトインのシェーダを作成
       shader = gpu.shader.from_builtin('UNIFORM_COLOR')

       #バッチを作成
       batch = gpu_extras.batch.batch_for_shader(shader, 'LINES', vertices, indices=indices)

       # シェーダのパラメータ設定
       color = [0.5,1.0,1.0,1.0]
       shader.bind()
       shader.uniform_float("color", color)
       # 描画
       batch.draw(shader)

class TOPBAR_MT_my_menu(bpy.types.Menu):
    bl_idname = "TOPBAR_MT_my_menu"
    bl_label = "世界演出エディタ" 
    bl_description = "拡張メニュー by " + bl_info["author"]

    def draw(self, context):
        layout = self.layout
        layout.operator("wm.url_open_preset", text="Manual", icon='HELP')
        layout.separator()
        layout.operator(AICSV_OT_import.bl_idname, text="CSVマップ読み込み", icon='FILE_BLANK')
        layout.separator()
        layout.operator(MYADDON_OT_stretch_vertex.bl_idname, text=MYADDON_OT_stretch_vertex.bl_label)
        layout.operator(MYADDON_OT_create_ico_sphere.bl_idname, text=MYADDON_OT_create_ico_sphere.bl_label)
        layout.operator(MYADDON_OT_export_scene.bl_idname, text=MYADDON_OT_export_scene.bl_label, icon='EXPORT')
        
    def submenu(self, context):
        self.layout.menu(TOPBAR_MT_my_menu.bl_idname)


classes = (
    AICSVMapMappingItem,
    AICSV_OT_mapping_add,
    AICSV_OT_mapping_remove,
    LevelEditorPreferences,
    AICSV_OT_generate,
    AICSV_OT_import,
    AICSV_PT_panel,
    MYADDON_OT_import_csv_map,
    MYADDON_OT_stretch_vertex,
    MYADDON_OT_create_ico_sphere,
    MYADDON_OT_export_scene,
    TOPBAR_MT_my_menu,
    MYADDON_OT_add_filename,
    OBJECT_PT_file_name,
    MYADDON_OT_add_collider,
    OBJECT_PT_collider,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_editor_menus.append(TOPBAR_MT_my_menu.submenu)


    # Scene properties
    bpy.types.Scene.ai_csv_mappings = bpy.props.CollectionProperty(
        type=AICSVMapMappingItem,
        name="マッピング設定",
    )
    bpy.types.Scene.ai_csv_prompt = bpy.props.StringProperty(
        name="Prompt",
        description="例: 横スクロールアクションゲーム用の面白いステージを作って。",
        default="横スクロールアクションゲーム用の面白いステージを作って。",
    )
    bpy.types.Scene.ai_csv_output_path = bpy.props.StringProperty(
        name="保存先",
        subtype="FILE_PATH",
        default="//ai_output.csv",
    )
    bpy.types.Scene.ai_csv_last_result = bpy.props.StringProperty(
        name="Last CSV Result",
        default="",
        options={"HIDDEN"},
    )
    bpy.types.Scene.ai_csv_status = bpy.props.StringProperty(
        name="Status",
        default="",
        options={"HIDDEN"},
    )
    bpy.types.Scene.ai_csv_is_running = bpy.props.BoolProperty(
        name="Generating",
        default=False,
        options={"HIDDEN"},
    )
    bpy.types.Scene.ai_csv_map_width = bpy.props.IntProperty(
        name="幅",
        description="生成するマップ의 幅 (列数)",
        default=20,
        min=1,
        max=500,
    )
    bpy.types.Scene.ai_csv_map_height = bpy.props.IntProperty(
        name="高さ",
        description="生成するマップ의 高さ (行数)",
        default=20,
        min=1,
        max=500,
    )
    bpy.types.Scene.ai_csv_char_wall = bpy.props.StringProperty(
        name="壁ブロック",
        description="壁 (ブロック) を表す文字",
        default="1",
    )
    bpy.types.Scene.ai_csv_char_empty = bpy.props.StringProperty(
        name="空白",
        description="空白 (何もない場所) を表す文字",
        default="0",
    )
    bpy.types.Scene.ai_csv_char_enemy = bpy.props.StringProperty(
        name="敵",
        description="敵の出現場所を表す文字",
        default="E",
    )
    bpy .types.TOPBAR_MT_editor_menus.append(TOPBAR_MT_my_menu.submenu)
    #3Dビューに描画関数を登録
    DrawCollider.handle = bpy.types.SpaceView3D.draw_handler_add(DrawCollider.draw_collider, (), 'WINDOW', 'POST_VIEW')
    print("レベルエディタ有効化")


def unregister():

    for prop in [
        "ai_csv_mappings", "ai_csv_char_enemy", "ai_csv_char_empty",
        "ai_csv_char_wall", "ai_csv_map_height", "ai_csv_map_width",
        "ai_csv_is_running", "ai_csv_status", "ai_csv_last_result",
        "ai_csv_output_path", "ai_csv_prompt"
    ]:
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)

    for cls in classes:
        bpy.utils.unregister_class(cls)

    bpy.types.TOPBAR_MT_editor_menus.remove(TOPBAR_MT_my_menu.submenu)
    #3Dビューから描画関数を削除
    if DrawCollider.handle:
        bpy.types.SpaceView3D.draw_handler_remove(DrawCollider.handle, 'WINDOW')

    try:
        bpy.types.TOPBAR_MT_editor_menus.remove(TOPBAR_MT_my_menu.submenu)
    except Exception:
        pass
        
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
    print("レベルエディタ無効化")


if __name__ == "__main__":
    register()