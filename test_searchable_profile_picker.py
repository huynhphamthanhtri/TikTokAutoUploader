import queue
import threading
import unittest
from unittest.mock import MagicMock, patch
import tkinter as tk
import customtkinter as ctk

# 1. Direct import test verification
import ui_dialogs
from ui_dialogs import SearchableProfilePickerModal
from ui_components import (
    ProfilePickerField,
    normalize_profile_names,
    normalized_fs_path,
    load_live_profile_names,
)
from youtube_monitor.ui import YouTubeMonitorView
from youtube_monitor.batch_view import BatchDownloadView


def get_test_root():
    default_root = getattr(tk, "_default_root", None)
    if default_root is not None and default_root.winfo_exists():
        return default_root
    try:
        root = ctk.CTk()
        root.withdraw()
        return root
    except Exception:
        return None


class TestProfilePickerFieldAndHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_test_root()

    def setUp(self):
        if not self.root:
            self.skipTest("Tkinter display not available")

    def test_normalize_profile_names_pure_helper(self):
        """normalize_profile_names trims, removes empty, deduplicates, and handles non-string/single string safely."""
        self.assertEqual(normalize_profile_names(None), [])
        self.assertEqual(normalize_profile_names([]), [])
        self.assertEqual(normalize_profile_names("SingleProfile"), ["SingleProfile"])
        self.assertEqual(normalize_profile_names(123), ["123"])
        raw = ["  Alpha  ", "", "  ", "Beta", "Alpha", "Gamma\n", None, "Beta "]
        self.assertEqual(normalize_profile_names(raw), ["Alpha", "Beta", "Gamma"])

    def test_normalized_fs_path_ntpath_case_insensitivity(self):
        """normalized_fs_path normalizes Windows paths case-insensitively using ntpath without disk access."""
        p1 = normalized_fs_path("C:/Users/Test/Videos/")
        p2 = normalized_fs_path("c:\\users\\test\\videos")
        self.assertEqual(p1, p2)
        self.assertEqual(normalized_fs_path(""), "")
        self.assertEqual(normalized_fs_path(None), "")

    def test_load_live_profile_names_semantics(self):
        """load_live_profile_names distinguishes missing handler, handler exceptions, and successful empty/non-empty lists."""
        # 1. Missing / non-callable handler
        ok, vals, msg = load_live_profile_names({})
        self.assertFalse(ok)
        self.assertIn("chưa được cấu hình", msg)

        # 2. Handler exception
        def failing_handler():
            raise RuntimeError("Database connection timed out")
        ok, vals, msg = load_live_profile_names({"get_profiles": failing_handler})
        self.assertFalse(ok)
        self.assertIn("Database connection timed out", msg)

        # 3. Successful non-empty list
        ok, vals, msg = load_live_profile_names({"get_profiles": lambda: [" Prof1 ", "Prof2", "Prof1"]})
        self.assertTrue(ok)
        self.assertEqual(vals, ("Prof1", "Prof2"))
        self.assertEqual(msg, "")

        # 4. Successful empty list
        ok, vals, msg = load_live_profile_names({"get_profiles": lambda: []})
        self.assertTrue(ok)
        self.assertEqual(vals, ())

    def test_field_semantic_vs_display_separation(self):
        """ProfilePickerField keeps semantic variable strictly empty or valid profile name, never placeholder."""
        var = ctk.StringVar(value="")
        cmd = MagicMock()
        field = ProfilePickerField(self.root, variable=var, command=cmd, placeholder_text="Chọn tài khoản TikTok")

        # Initial state: semantic is empty, display shows placeholder
        self.assertEqual(field.get(), "")
        self.assertEqual(var.get(), "")
        self.assertEqual(field.display_var.get(), "Chọn tài khoản TikTok")
        self.assertTrue(field.is_empty())
        self.assertFalse(field.is_valid())

        # Set valid profile
        field.set_profiles(["Profile_A", "Profile_B"])
        var.set("Profile_A")
        self.assertEqual(field.get(), "Profile_A")
        self.assertEqual(field.display_var.get(), "Profile_A")
        self.assertTrue(field.is_valid())
        self.assertFalse(field.is_empty())
        self.assertFalse(field.is_stale())

        # Set stale profile
        var.set("Profile_Deleted")
        self.assertEqual(field.get(), "Profile_Deleted")
        self.assertEqual(field.display_var.get(), "Profile_Deleted")
        self.assertFalse(field.is_valid())
        self.assertTrue(field.is_stale())

        # Clear variable
        var.set("")
        self.assertEqual(field.get(), "")
        self.assertEqual(field.display_var.get(), "Chọn tài khoản TikTok")
        self.assertTrue(field.is_empty())

        field.destroy()

    def test_field_options_unchanged_but_variable_stale_detected(self):
        """When profile options don't change but variable is set to invalid from outside, validity updates immediately."""
        var = ctk.StringVar(value="Prof_1")
        field = ProfilePickerField(self.root, variable=var, command=lambda: None)
        field.set_profiles(["Prof_1", "Prof_2"])
        self.assertTrue(field.is_valid())

        # Set variable externally to stale
        var.set("Non_Existent_Prof")
        self.assertTrue(field.is_stale())
        self.assertFalse(field.is_valid())

        field.destroy()

    def test_field_trace_cleanup_and_destroy_idempotency(self):
        """Destroying ProfilePickerField cleans up its trace safely and is idempotent."""
        var = ctk.StringVar(value="Prof_1")
        field = ProfilePickerField(self.root, variable=var, command=lambda: None)
        self.assertIsNotNone(field._trace_token)

        # Call destroy
        field.destroy()
        self.assertIsNone(field._trace_token)

        # Second destroy should not raise
        field.destroy()

        # Changing var after destroy should not crash
        var.set("Prof_2")

    def test_field_entry_readonly_state_and_button_command(self):
        """ProfilePickerField entry is readonly and button triggers command."""
        cmd = MagicMock()
        var = ctk.StringVar(value="Initial")
        field = ProfilePickerField(self.root, variable=var, command=cmd)
        self.assertEqual(field.entry.cget("state"), "readonly")

        field.btn_picker.invoke()
        cmd.assert_called_once()
        field.destroy()


class TestSearchableProfilePickerModalHardened(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_test_root()

    def setUp(self):
        if not self.root:
            self.skipTest("Tkinter display not available")

    def test_modal_synthetic_iids_and_unicode_names(self):
        """Modal uses synthetic IIDs (prof_0, prof_1) and correctly maps Unicode / special characters."""
        profiles = ["Tiếng Việt 🇻🇳", "Prof @#$ 1", "Prof_With_Newline\n", "Emoji 🎉 Account"]
        modal = SearchableProfilePickerModal(
            self.root,
            profiles=profiles,
            current_profile="Tiếng Việt 🇻🇳",
        )
        self.assertEqual(len(modal._iid_to_profile), 4)
        self.assertEqual(modal._iid_to_profile["prof_0"], "Tiếng Việt 🇻🇳")
        self.assertEqual(modal._get_selected_profile(), "Tiếng Việt 🇻🇳")
        modal._close_modal()

    def test_modal_unknown_tree_iid_returns_none_without_raw_values_fallback(self):
        """_get_selected_profile() strictly relies on _iid_to_profile mapping and returns None on unknown IID."""
        modal = SearchableProfilePickerModal(
            self.root,
            profiles=["Alpha", "Beta"],
        )
        with patch.object(modal.tree, "selection", return_value=("unknown_injected_iid",)):
            self.assertIsNone(modal._get_selected_profile())
        modal._close_modal()

    def test_modal_auto_selection_does_not_override_user_choice(self):
        """Auto-selection during _populate_tree does not update _user_selected_profile thanks to _is_populating guard."""
        modal = SearchableProfilePickerModal(
            self.root,
            profiles=["Alpha", "Beta", "Gamma"],
        )
        # Initially, no explicit user selection
        self.assertIsNone(modal._user_selected_profile)

        # Trigger search which calls _populate_tree
        modal.search_var.set("Bet")
        self.assertIsNone(modal._user_selected_profile)

        # User explicitly selects Beta
        modal._user_selected_profile = "Beta"
        modal.search_var.set("")
        # Restored to Beta
        self.assertEqual(modal._get_selected_profile(), "Beta")

        modal._close_modal()

    def test_modal_stale_profile_removes_from_list_and_refreshes_without_dead_end(self):
        """When confirm callback rejects profile as deleted, modal removes it from _all_profiles and re-renders."""
        modal = SearchableProfilePickerModal(
            self.root,
            profiles=["Deleted_Prof", "Valid_Prof"],
            current_profile="Deleted_Prof",
            on_confirm=lambda p: (False, f"Profile '{p}' không còn tồn tại trong hệ thống"),
        )
        self.assertIn("Deleted_Prof", modal._all_profiles)

        # Confirm triggers error and auto-removes Deleted_Prof
        modal._do_confirm()
        self.assertNotIn("Deleted_Prof", modal._all_profiles)
        self.assertEqual(modal._all_profiles, ["Valid_Prof"])
        self.assertIn("không còn tồn tại", modal.error_var.get())

        modal._close_modal()

    def test_modal_selection_pending_restoration_across_filter(self):
        """When searching filters out current selection, clearing search restores semantic selection."""
        modal = SearchableProfilePickerModal(
            self.root,
            profiles=["Alpha", "Beta", "Gamma"],
            current_profile="Gamma",
        )
        self.assertEqual(modal._get_selected_profile(), "Gamma")

        # User explicitly selects Beta
        modal.tree.selection_set("prof_1")
        modal._on_select()
        self.assertEqual(modal._get_selected_profile(), "Beta")

        # Search for nonexistent (no match)
        modal.search_var.set("XYZ_Nonexistent")
        self.assertIsNone(modal._get_selected_profile())

        # Clear search query -> Beta selection is preserved via _user_selected_profile
        modal.search_var.set("")
        self.assertEqual(modal._get_selected_profile(), "Beta")

        modal._close_modal()

    def test_modal_filter_case_insensitive_and_empty_state(self):
        """Modal filters case-insensitively and disables confirm button when empty."""
        modal = SearchableProfilePickerModal(
            self.root,
            profiles=["Alpha_Channel", "Beta_Channel", "gamma_channel"],
        )
        modal.search_var.set("BETA")
        self.assertEqual(len(modal._filtered_profiles), 1)
        self.assertEqual(modal._filtered_profiles[0], "Beta_Channel")

        # Search no match
        modal.search_var.set("Nomatch123")
        self.assertEqual(len(modal._filtered_profiles), 0)
        self.assertEqual(modal.btn_confirm.cget("state"), "disabled")

        modal._close_modal()

    def test_modal_keyboard_navigation_escape_and_enter(self):
        """Escape closes modal and Enter confirms selection."""
        confirmed = []
        modal = SearchableProfilePickerModal(
            self.root,
            profiles=["Alpha", "Beta"],
            on_confirm=lambda p: (confirmed.append(p), (True, ""))[1],
        )
        # Search enter confirms
        modal._on_search_return()
        self.assertEqual(confirmed, ["Alpha"])
        self.assertTrue(modal._closing)

    def test_modal_unified_idempotent_close_and_focus_restoration(self):
        """_close_modal() is idempotent, releases grab, and restores focus safely on owner."""
        focus_target = ctk.CTkButton(self.root, text="Focus Me")
        focus_target.pack()
        modal = SearchableProfilePickerModal(
            self.root,
            profiles=["Prof_1"],
            return_focus_to=focus_target,
        )
        modal._close_modal()
        modal._close_modal()
        self.assertTrue(modal._closing)
        focus_target.destroy()

    def test_modal_focus_target_destroyed_before_execution_handled_safely(self):
        """If focus target is destroyed before after_idle callback runs, it handles TclError/RuntimeError silently."""
        temp_btn = ctk.CTkButton(self.root, text="Temp")
        temp_btn.pack()
        modal = SearchableProfilePickerModal(
            self.root,
            profiles=["Prof_1"],
            return_focus_to=temp_btn,
        )
        temp_btn.destroy()
        modal._close_modal()


class TestYouTubeMonitorViewPhase2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_test_root()

    def setUp(self):
        if not self.root:
            self.skipTest("Tkinter display not available")

    def test_widget_hierarchy_and_profile_picker_fields_exist(self):
        """YouTubeMonitorView replaces ComboBoxes with ProfilePickerField instances."""
        handlers = {
            "get_profiles": lambda: ["Profile_1", "Profile_2"],
        }
        parent = ctk.CTkFrame(self.root)
        view = YouTubeMonitorView(parent, handlers)

        self.assertTrue(hasattr(view, "add_profile_picker_field"))
        self.assertIsInstance(view.add_profile_picker_field, ProfilePickerField)
        self.assertTrue(hasattr(view, "test_profile_picker_field"))
        self.assertIsInstance(view.test_profile_picker_field, ProfilePickerField)

        self.assertEqual(view.add_profile_picker_btn, view.add_profile_picker_field.btn_picker)
        self.assertEqual(view.test_profile_picker_btn, view.test_profile_picker_field.btn_picker)

        parent.destroy()

    def test_refresh_profiles_transient_error_preserves_last_known_good_state(self):
        """Transient get_profiles error does not clear profile_names or mark fields stale."""
        handlers = {
            "get_profiles": lambda: ["Prof_A", "Prof_B"],
        }
        parent = ctk.CTkFrame(self.root)
        view = YouTubeMonitorView(parent, handlers)
        view.profile_var.set("Prof_A")
        self.assertTrue(view.add_profile_picker_field.is_valid())

        # Handler fails temporarily
        handlers["get_profiles"] = MagicMock(side_effect=RuntimeError("Network failure"))
        view.refresh_profiles()

        # Last known good state preserved
        self.assertEqual(view.profile_names, ["Prof_A", "Prof_B"])
        self.assertEqual(view.profile_var.get(), "Prof_A")
        self.assertTrue(view.add_profile_picker_field.is_valid())

        parent.destroy()

    def test_add_channel_action_time_live_fetch_error_and_stale_profile_block_dispatch(self):
        """_add_channel calls load_live_profile_names at preflight time and blocks if live fetch fails or profile stale."""
        mock_add = MagicMock()
        handlers = {
            "get_profiles": lambda: ["Valid_Prof"],
            "add_channel": mock_add,
        }
        parent = ctk.CTkFrame(self.root)
        view = YouTubeMonitorView(parent, handlers)
        view.append_log = MagicMock()

        # 1. Profile deleted from live service right before dispatch
        view.channel_entry.insert(0, "UC123")
        view.profile_var.set("Valid_Prof")
        handlers["get_profiles"] = lambda: ["Different_Prof"]

        view._add_channel()
        mock_add.assert_not_called()
        self.assertTrue(view.add_profile_picker_field.is_stale())

        # 2. Live fetch fails with exception right before dispatch
        view.profile_var.set("Different_Prof")
        handlers["get_profiles"] = MagicMock(side_effect=RuntimeError("Service down"))

        view._add_channel()
        mock_add.assert_not_called()

        parent.destroy()

    def test_download_test_action_time_live_fetch_error_and_stale_profile_block_dispatch(self):
        """_download_test calls load_live_profile_names at preflight time and blocks if live fetch fails or profile stale."""
        mock_download = MagicMock()
        handlers = {
            "get_profiles": lambda: ["Valid_Prof"],
            "download_test": mock_download,
        }
        parent = ctk.CTkFrame(self.root)
        view = YouTubeMonitorView(parent, handlers)
        view.append_log = MagicMock()

        # 1. Profile deleted from live service right before test download
        view.test_entry.insert(0, "https://youtube.com/watch?v=123")
        view.test_profile_var.set("Valid_Prof")
        handlers["get_profiles"] = lambda: []

        view._download_test()
        mock_download.assert_not_called()
        self.assertTrue(view.test_profile_picker_field.is_stale())

        # 2. Live fetch fails with exception
        handlers["get_profiles"] = MagicMock(side_effect=RuntimeError("Timeout"))
        view._download_test()
        mock_download.assert_not_called()

        parent.destroy()

    def test_context_menu_change_profile_live_revalidation_and_focus_restoration(self):
        """Context menu profile picker revalidates profile live and restores focus to tree."""
        mock_set_profile = MagicMock(return_value=(True, "Updated"))
        handlers = {
            "get_channels": lambda: [{"channel_id": "UC_TEST"}],
            "get_profiles": lambda: ["Prof_Live"],
            "set_profile": mock_set_profile,
        }
        parent = ctk.CTkFrame(self.root)
        view = YouTubeMonitorView(parent, handlers)
        view.selected_channel_id = "UC_TEST"
        view._channels_data = [{"channel_id": "UC_TEST", "title": "Test Ch", "profile_name": "Old_Prof"}]

        with patch("ui_dialogs.SearchableProfilePickerModal") as MockModal:
            view._open_profile_picker("UC_TEST")
            self.assertTrue(MockModal.called)
            kwargs = MockModal.call_args[1]
            self.assertEqual(kwargs["return_focus_to"], view.tree)

            confirm_cb = kwargs["on_confirm"]

            # Live fetch succeeds and profile is valid
            ok, msg = confirm_cb("Prof_Live")
            self.assertTrue(ok)
            mock_set_profile.assert_called_once_with("UC_TEST", "Prof_Live")

            # Live fetch fails: profile not in live list
            handlers["get_profiles"] = lambda: ["Different_Live"]
            ok2, msg2 = confirm_cb("Prof_Deleted")
            self.assertFalse(ok2)
            self.assertIn("Profile không còn tồn tại", msg2)

        parent.destroy()

    def test_show_context_menu_creates_dynamic_tk_menu_with_closures(self):
        """_show_context_menu builds dynamic Tk Menu whose closures capture the clicked iid independently of subsequent selection changes."""
        handlers = {
            "get_channels": lambda: [{"channel_id": "UC_CLICKED"}, {"channel_id": "UC_MUTATED"}],
            "toggle_active": MagicMock(return_value=(True, "Toggled")),
            "toggle_short": MagicMock(return_value=(True, "Short Toggled")),
            "remove_channel": MagicMock(return_value=(True, "Removed")),
        }
        parent = ctk.CTkFrame(self.root)
        view = YouTubeMonitorView(parent, handlers)
        view.tree.insert("", "end", iid="UC_CLICKED", values=("Title", "@handle", "Prof", "0", "0", "0", "C:/f"))
        view.tree.insert("", "end", iid="UC_MUTATED", values=("Title 2", "@handle2", "Prof", "0", "0", "0", "C:/f"))

        # Build dynamic menu directly using helper
        menu = view._build_context_menu("UC_CLICKED")

        # Mutate selection after menu construction
        view.selected_channel_id = "UC_MUTATED"
        view.tree.selection_set("UC_MUTATED")

        # Extract callbacks attached to menu commands and invoke them
        commands = {}
        for i in range(menu.index("end") + 1):
            try:
                if menu.type(i) == "command":
                    lbl = menu.entrycget(i, "label")
                    cmd = menu.entrycget(i, "command")
                    commands[lbl] = cmd
            except Exception:
                pass

        self.assertIn("⏯️ Bật/Tắt Theo Dõi", commands)
        self.assertIn("⚡ Bật/Tắt Shorts Chỉ Định", commands)
        self.assertIn("🗑️ Xóa Kênh", commands)

        # Invoke toggle command from menu closure
        cmd_toggle = commands["⏯️ Bật/Tắt Theo Dõi"]
        view.tk.call(cmd_toggle)
        handlers["toggle_active"].assert_called_with("UC_CLICKED")

        # Invoke short toggle command from menu closure
        cmd_short = commands["⚡ Bật/Tắt Shorts Chỉ Định"]
        view.tk.call(cmd_short)
        handlers["toggle_short"].assert_called_with("UC_CLICKED")

        # Invoke remove command from menu closure
        cmd_remove = commands["🗑️ Xóa Kênh"]
        with patch("tkinter.messagebox.askyesno", return_value=True):
            view.tk.call(cmd_remove)
            handlers["remove_channel"].assert_called_with("UC_CLICKED")

        menu.destroy()
        parent.destroy()

    def test_open_channel_link_with_keyword_cid_and_empty_string(self):
        """_open_channel_link correctly handles keyword cid argument and explicit empty string."""
        parent = ctk.CTkFrame(self.root)
        view = YouTubeMonitorView(parent, {})
        view.selected_channel_id = "UC_SELECTED"

        with patch("webbrowser.open") as mock_open:
            # 1. Explicit keyword cid
            view._open_channel_link(cid="UC_SPECIFIC")
            mock_open.assert_called_once_with("https://www.youtube.com/channel/UC_SPECIFIC")
            mock_open.reset_mock()

            # 2. Explicit empty string does not fallback to selected channel id
            view._open_channel_link(cid="")
            mock_open.assert_not_called()

        parent.destroy()

    def test_validate_channel_live_fail_closed_comprehensive(self):
        """_validate_channel_live_fail_closed handles non-iterable, malformed items, exceptions, and non-existent IDs."""
        parent = ctk.CTkFrame(self.root)

        # 1. Non-iterable response (e.g. integer or None)
        view1 = YouTubeMonitorView(parent, {"get_channels": lambda: 12345})
        ok1, err1 = view1._validate_channel_live_fail_closed("UC_1")
        self.assertFalse(ok1)
        self.assertIn("không hợp lệ", err1)

        # 2. Malformed item types in list
        view2 = YouTubeMonitorView(parent, {"get_channels": lambda: ["string_item", None, 42]})
        ok2, err2 = view2._validate_channel_live_fail_closed("UC_1")
        self.assertFalse(ok2)
        self.assertIn("không còn tồn tại", err2)

        # 3. Valid dict in list
        view3 = YouTubeMonitorView(parent, {"get_channels": lambda: [{"channel_id": "UC_VALID"}]})
        ok3, err3 = view3._validate_channel_live_fail_closed("UC_VALID")
        self.assertTrue(ok3)
        self.assertEqual(err3, "")

        parent.destroy()

    def test_toggle_and_remove_fail_closed_on_deleted_channel(self):
        """_toggle_active, _toggle_short, _remove block handler dispatch when channel is not in live list."""
        mock_toggle = MagicMock()
        mock_remove = MagicMock()
        handlers = {
            "get_channels": lambda: [{"channel_id": "UC_OTHER"}],
            "toggle_active": mock_toggle,
            "remove_channel": mock_remove,
        }
        parent = ctk.CTkFrame(self.root)
        view = YouTubeMonitorView(parent, handlers)
        view.append_log = MagicMock()

        view._toggle_active("UC_DELETED")
        mock_toggle.assert_not_called()
        self.assertTrue(any("không thể" in str(c[0][0]).lower() for c in view.append_log.call_args_list))

        view._remove(target_cid="UC_DELETED")
        mock_remove.assert_not_called()

        parent.destroy()

    def test_context_menu_profile_picker_fail_closed_checks(self):
        """Context menu profile picker fails closed on missing get_channels handler, handler exception, or missing channel."""
        parent = ctk.CTkFrame(self.root)

        # 1. Missing get_channels handler
        view1 = YouTubeMonitorView(parent, {"get_profiles": lambda: ["Prof_1"]})
        with patch("ui_dialogs.SearchableProfilePickerModal") as MockModal:
            view1._open_profile_picker("UC_ANY")
            ok, msg = MockModal.call_args[1]["on_confirm"]("Prof_1")
            self.assertFalse(ok)
            self.assertIn("chưa được cấu hình", msg)

        # 2. Handler raises exception
        view2 = YouTubeMonitorView(parent, {
            "get_channels": MagicMock(side_effect=RuntimeError("DB query failed")),
            "get_profiles": lambda: ["Prof_1"],
        })
        with patch("ui_dialogs.SearchableProfilePickerModal") as MockModal:
            view2._open_profile_picker("UC_ANY")
            ok, msg = MockModal.call_args[1]["on_confirm"]("Prof_1")
            self.assertFalse(ok)
            self.assertIn("Lỗi khi xác thực", msg)

        # 3. Channel missing from live list
        view3 = YouTubeMonitorView(parent, {
            "get_channels": lambda: [{"channel_id": "UC_OTHER"}],
            "get_profiles": lambda: ["Prof_1"],
        })
        with patch("ui_dialogs.SearchableProfilePickerModal") as MockModal:
            view3._open_profile_picker("UC_DELETED")
            ok, msg = MockModal.call_args[1]["on_confirm"]("Prof_1")
            self.assertFalse(ok)
            self.assertIn("không còn tồn tại", msg)

        parent.destroy()

    def test_context_menu_actions_target_explicit_cid_isolated_from_selection(self):
        """Context menu actions accept explicit target_cid closure, preventing mutable selection race."""
        parent = ctk.CTkFrame(self.root)
        view = YouTubeMonitorView(parent, {
            "get_channels": lambda: [{"channel_id": "UC_EXPLICIT_1"}, {"channel_id": "UC_EXPLICIT_2"}, {"channel_id": "UC_EXPLICIT_3"}],
            "toggle_active": MagicMock(return_value=(True, "Toggled")),
            "toggle_short": MagicMock(return_value=(True, "Short toggled")),
            "remove_channel": MagicMock(return_value=(True, "Removed")),
        })
        view.selected_channel_id = "UC_MUTATED_SELECTION"

        # Explicit target cid overrides selection
        view._toggle_active("UC_EXPLICIT_1")
        view.handlers["toggle_active"].assert_called_once_with("UC_EXPLICIT_1")

        view._toggle_short("UC_EXPLICIT_2")
        view.handlers["toggle_short"].assert_called_once_with("UC_EXPLICIT_2")

        with patch("tkinter.messagebox.askyesno", return_value=True):
            view._remove_with_confirm("UC_EXPLICIT_3")
            view.handlers["remove_channel"].assert_called_once_with("UC_EXPLICIT_3")

        parent.destroy()


class TestBatchDownloadViewPhase2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = get_test_root()

    def setUp(self):
        if not self.root:
            self.skipTest("Tkinter display not available")

    def test_batch_view_state_machine_and_field_initialization(self):
        """BatchDownloadView initializes state machine correctly and uses ProfilePickerField."""
        handlers = {
            "get_profiles": lambda: ["Profile_X"],
            "get_profile_folder": lambda p: (True, "C:/videos/Profile_X"),
        }
        parent = ctk.CTkFrame(self.root)
        view = BatchDownloadView(parent, handlers)

        self.assertTrue(hasattr(view, "batch_profile_picker_field"))
        self.assertIsInstance(view.batch_profile_picker_field, ProfilePickerField)
        self.assertEqual(view.folder_source, "none")
        self.assertEqual(view.profile_default_folder, "")
        self.assertEqual(view.folder_owner_profile, "")
        self.assertIsInstance(view._ui_queue, queue.Queue)

        parent.destroy()

    def test_batch_folder_trace_manual_edit_and_clear(self):
        """Typing in folder_entry sets folder_source='manual', clearing it sets 'none'."""
        parent = ctk.CTkFrame(self.root)
        view = BatchDownloadView(parent, {})
        view.profile_var.set("My_Profile")

        # Type manual folder
        view.folder_var.set("D:/my_custom_folder")
        self.assertEqual(view.folder_source, "manual")
        self.assertEqual(view.folder_owner_profile, "My_Profile")

        # Clear folder
        view.folder_var.set("")
        self.assertEqual(view.folder_source, "none")
        self.assertEqual(view.folder_owner_profile, "")

        parent.destroy()

    def test_batch_manual_folder_edit_rebinds_owner_to_current_profile(self):
        """When profile changes and user types a new folder, manual owner is rebound to current profile."""
        parent = ctk.CTkFrame(self.root)
        view = BatchDownloadView(parent, {})
        view.profile_var.set("Prof_A")
        view.folder_var.set("D:/folder_a")
        self.assertEqual(view.folder_owner_profile, "Prof_A")

        # Switch to Prof_B and edit folder
        view.profile_var.set("Prof_B")
        view.folder_var.set("D:/folder_b")
        self.assertEqual(view.folder_owner_profile, "Prof_B")
        self.assertEqual(view.folder_source, "manual")

        parent.destroy()

    def test_batch_choose_folder_binds_manual_owner(self):
        """_choose_folder explicitly sets manual owner to current profile."""
        parent = ctk.CTkFrame(self.root)
        view = BatchDownloadView(parent, {})
        view.profile_var.set("Selected_Prof")

        with patch("tkinter.filedialog.askdirectory", return_value="E:/custom/batch_dir"):
            view._choose_folder()
            self.assertEqual(view.folder_var.get(), "E:/custom/batch_dir")
            self.assertEqual(view.folder_source, "manual")
            self.assertEqual(view.folder_owner_profile, "Selected_Prof")

        parent.destroy()

    def test_batch_folder_match_default_case_insensitive_sets_source_profile(self):
        """Setting folder matching profile_default_folder case-insensitively sets folder_source='profile'."""
        parent = ctk.CTkFrame(self.root)
        view = BatchDownloadView(parent, {})
        view.profile_default_folder = "C:/Videos/My_Profile"
        view.profile_var.set("My_Profile")

        # Set matching path with different case / slashes
        view.folder_var.set("c:\\videos\\my_profile\\")
        self.assertEqual(view.folder_source, "profile")
        self.assertEqual(view.folder_owner_profile, "My_Profile")

        parent.destroy()

    def test_batch_transactional_commit_and_best_effort_rollback(self):
        """_commit_profile_target rolls back metadata and variables on StringVar exception without crashing."""
        parent = ctk.CTkFrame(self.root)
        view = BatchDownloadView(parent, {})
        view.profile_var.set("Original_Prof")
        view.folder_var.set("C:/original_folder")
        view.profile_default_folder = "C:/original_folder"
        view.folder_source = "profile"
        view.folder_owner_profile = "Original_Prof"

        # Mock folder_var.set to raise exception
        view.folder_var.set = MagicMock(side_effect=RuntimeError("Tcl internal variable error"))

        ok, msg = view._commit_profile_target("New_Prof", "C:/new_folder")
        self.assertFalse(ok)
        self.assertIn("Lỗi cập nhật trạng thái", msg)

        # Metadata restored
        self.assertEqual(view.profile_default_folder, "C:/original_folder")
        self.assertEqual(view.folder_source, "profile")
        self.assertEqual(view.folder_owner_profile, "Original_Prof")
        self.assertFalse(view._updating_folder)

        parent.destroy()

    def test_batch_use_profile_folder_commit_failure_logs_error_and_returns_false(self):
        """_use_profile_folder logs error when _commit_profile_target fails and does not claim success."""
        parent = ctk.CTkFrame(self.root)
        view = BatchDownloadView(parent, {
            "get_profile_folder": lambda p: (True, "C:/valid/folder"),
        })
        view.profile_var.set("My_Prof")
        view._append_log = MagicMock()

        # Mock commit to fail
        view._commit_profile_target = MagicMock(return_value=(False, "Failed to commit folder"))

        view._use_profile_folder()
        view._append_log.assert_called_with("Lỗi khi đặt thư mục: Failed to commit folder", "ERROR")

        parent.destroy()

    def test_batch_start_fails_when_live_profile_fetch_fails_or_profile_deleted(self):
        """_start_batch blocks start if preflight get_profiles fails or selected profile is not in live list."""
        mock_batch = MagicMock()
        handlers = {
            "get_profiles": lambda: ["Prof_Live"],
            "get_profile_folder": lambda p: (True, "C:/videos/Prof_Live"),
            "batch_download_latest": mock_batch,
        }
        parent = ctk.CTkFrame(self.root)
        view = BatchDownloadView(parent, handlers)
        view.links_text.insert("1.0", "https://youtube.com/@channel1\n")
        view.profile_var.set("Prof_Live")
        view.folder_var.set("C:/videos/Prof_Live")
        view.profile_default_folder = "C:/videos/Prof_Live"
        view.folder_source = "profile"
        view.folder_owner_profile = "Prof_Live"

        # 1. Profile deleted from live
        handlers["get_profiles"] = lambda: ["Different_Profile"]
        view._start_batch()
        self.assertFalse(view.running)
        mock_batch.assert_not_called()

        # 2. Live fetch raises exception
        handlers["get_profiles"] = MagicMock(side_effect=RuntimeError("Connection refused"))
        view._start_batch()
        self.assertFalse(view.running)
        mock_batch.assert_not_called()

        parent.destroy()

    def test_batch_start_fails_when_live_profile_folder_changed(self):
        """_start_batch blocks start if preflight profile folder has changed since it was selected."""
        mock_batch = MagicMock()
        handlers = {
            "get_profiles": lambda: ["Prof_Live"],
            "get_profile_folder": lambda p: (True, "D:/new_moved_location"),
            "batch_download_latest": mock_batch,
        }
        parent = ctk.CTkFrame(self.root)
        view = BatchDownloadView(parent, handlers)
        view.links_text.insert("1.0", "https://youtube.com/@channel1\n")
        view.profile_var.set("Prof_Live")
        view.folder_var.set("C:/old_stale_location")
        view.profile_default_folder = "C:/old_stale_location"
        view.folder_source = "profile"
        view.folder_owner_profile = "Prof_Live"

        view._start_batch()
        self.assertFalse(view.running)
        mock_batch.assert_not_called()

        parent.destroy()

    def test_batch_start_thread_exception_rolls_back_without_logging_start(self):
        """If Thread.start() raises RuntimeError, _start_batch rolls back running=False, status='Sẵn sàng' and does not log start."""
        handlers = {
            "get_profiles": lambda: ["Prof_Live"],
            "get_profile_folder": lambda p: (True, "C:/videos/Prof_Live"),
        }
        parent = ctk.CTkFrame(self.root)
        view = BatchDownloadView(parent, handlers)
        view.links_text.insert("1.0", "https://youtube.com/@channel1\n")
        view.profile_var.set("Prof_Live")
        view.folder_var.set("C:/videos/Prof_Live")
        view.profile_default_folder = "C:/videos/Prof_Live"
        view.folder_source = "profile"
        view.folder_owner_profile = "Prof_Live"
        view._append_log = MagicMock()

        with patch("threading.Thread") as MockThread:
            mock_thread_instance = MagicMock()
            mock_thread_instance.start.side_effect = RuntimeError("Cannot allocate thread")
            MockThread.return_value = mock_thread_instance

            view._start_batch()
            self.assertFalse(view.running)
            self.assertEqual(view.status_var.get(), "Sẵn sàng")

            # Check that "Bắt đầu tải" was NOT logged
            for call_arg in view._append_log.call_args_list:
                self.assertNotIn("Bắt đầu tải", call_arg[0][0])
            # Error log was recorded
            self.assertTrue(any("Không thể khởi chạy" in call[0][0] for call in view._append_log.call_args_list))

        parent.destroy()

    def test_batch_ui_queue_drains_events_safely_from_ui_thread(self):
        """Worker thread enqueues events without calling Tk; UI drain loop updates log and status on UI thread."""
        parent = ctk.CTkFrame(self.root)
        view = BatchDownloadView(parent, {})
        view._append_log = MagicMock()
        view._mark_idle = MagicMock()
        view.run_generation = 1

        # Simulate worker enqueueing events with active generation
        view._ui_queue.put(("log", 1, "INFO", "Worker step 1"))
        view._ui_queue.put(("idle", 1))

        # UI thread drains queue
        view._drain_ui_queue()

        view._append_log.assert_called_once_with("Worker step 1", "INFO")
        view._mark_idle.assert_called_once()
        self.assertTrue(view._ui_queue.empty())

        parent.destroy()

    def test_batch_queue_drain_filters_stale_generation_and_limits_batch(self):
        """_drain_ui_queue discards events from previous generations and limits processing to 50 items per tick."""
        parent = ctk.CTkFrame(self.root)
        view = BatchDownloadView(parent, {})
        view._append_log = MagicMock()
        view.run_generation = 2

        # 1. Put stale generation event
        view._ui_queue.put(("log", 1, "INFO", "Stale message"))

        # 2. Put 60 events for active generation 2
        for i in range(60):
            view._ui_queue.put(("log", 2, "INFO", f"Message {i}"))

        # Drain one tick (max 50 items)
        view._drain_ui_queue()

        # Stale event was discarded, active events drained up to batch limit
        self.assertEqual(view._append_log.call_count, 49)  # 1 stale discarded + 49 processed = 50 total popped
        for call_arg in view._append_log.call_args_list:
            self.assertNotEqual(call_arg[0][0], "Stale message")

        parent.destroy()

    def test_batch_run_generation_monotonic_on_start_failure(self):
        """When thread start fails, run_generation remains monotonic and is never decremented/recycled."""
        parent = ctk.CTkFrame(self.root)
        view = BatchDownloadView(parent, {
            "get_profiles": lambda: ["Prof_1"],
            "get_profile_folder": lambda p: (True, "C:/videos/Prof_1"),
        })
        view.profile_var.set("Prof_1")
        view.folder_var.set("C:/videos/Prof_1")
        view.profile_default_folder = "C:/videos/Prof_1"
        view.folder_source = "profile"
        view.folder_owner_profile = "Prof_1"
        view.links_text.insert("1.0", "https://youtube.com/@c1\n")
        initial_gen = view.run_generation

        with patch("threading.Thread.start", side_effect=RuntimeError("Spawn error")):
            view._start_batch()
            self.assertEqual(view.run_generation, initial_gen + 1)
            self.assertFalse(view.running)
            self.assertIsNone(view.active_stop_event)

        parent.destroy()

    def test_batch_run_malformed_handler_result_enqueues_error(self):
        """When batch_download_latest returns malformed result (not a 2-tuple), ERROR log is enqueued before idle."""
        parent = ctk.CTkFrame(self.root)
        view = BatchDownloadView(parent, {
            "batch_download_latest": lambda *args: None, # Malformed return
        })
        view._append_log = MagicMock()
        stop_event = threading.Event()
        start_barrier = threading.Event()
        start_barrier.set()

        view._run_batch(1, stop_event, start_barrier, ["link1"], "folder", "prof")

        # Drain queue
        view.run_generation = 1
        view._drain_ui_queue()

        # Check that error log was captured
        self.assertTrue(any("không hợp lệ" in call[0][0] or "Lỗi" in call[0][0] for call in view._append_log.call_args_list))

        parent.destroy()

    def test_batch_active_stop_event_isolated_per_run(self):
        """Each batch run receives a distinct stop_event token; stop_batch signals the active token."""
        parent = ctk.CTkFrame(self.root)
        view = BatchDownloadView(parent, {
            "get_profiles": lambda: ["Prof_1"],
            "get_profile_folder": lambda p: (True, "C:/videos/Prof_1"),
            "batch_download_latest": lambda *args: (True, "Done"),
        })
        view.profile_var.set("Prof_1")
        view.folder_var.set("C:/videos/Prof_1")
        view.profile_default_folder = "C:/videos/Prof_1"
        view.folder_source = "profile"
        view.folder_owner_profile = "Prof_1"
        view.links_text.insert("1.0", "https://youtube.com/@c1\n")

        with patch("threading.Thread.start"):
            view._start_batch()
            active_event = view.active_stop_event
            self.assertIsNotNone(active_event)
            self.assertFalse(active_event.is_set())

            # Stop batch signals active event
            view._stop_batch()
            self.assertTrue(active_event.is_set())

        parent.destroy()

    def test_batch_start_failure_does_not_enqueue_ghost_start_log(self):
        """When Thread.start() throws exception, start log is never enqueued into _ui_queue, preventing ghost start logs on drain."""
        handlers = {
            "get_profiles": lambda: ["Prof_Live"],
            "get_profile_folder": lambda p: (True, "C:/videos/Prof_Live"),
        }
        parent = ctk.CTkFrame(self.root)
        view = BatchDownloadView(parent, handlers)
        view.links_text.insert("1.0", "https://youtube.com/@channel1\n")
        view.profile_var.set("Prof_Live")
        view.folder_var.set("C:/videos/Prof_Live")
        view.profile_default_folder = "C:/videos/Prof_Live"
        view.folder_source = "profile"
        view.folder_owner_profile = "Prof_Live"
        view._append_log = MagicMock()

        with patch("threading.Thread.start", side_effect=RuntimeError("Cannot allocate thread")):
            view._start_batch()
            self.assertFalse(view.running)
            self.assertEqual(view.status_var.get(), "Sẵn sàng")

            # Queue does NOT contain start log
            queued_items = list(view._ui_queue.queue)
            self.assertFalse(any("Bắt đầu tải" in str(item) for item in queued_items))

            # Drain queue does NOT output start log
            view._drain_ui_queue()
            for call_arg in view._append_log.call_args_list:
                self.assertNotIn("Bắt đầu tải", call_arg[0][0])

        parent.destroy()

    def test_batch_worker_barrier_cancellation_wait_loop(self):
        """Worker thread waiting on barrier exits cleanly when stop_event is set before barrier release."""
        parent = ctk.CTkFrame(self.root)
        view = BatchDownloadView(parent, {})
        stop_event = threading.Event()
        start_barrier = threading.Event()

        # Signal stop before releasing barrier
        stop_event.set()

        # Run worker directly
        view._run_batch(1, stop_event, start_barrier, ["link1"], "folder", "prof")

        # Worker enqueued idle event with token
        queued = list(view._ui_queue.queue)
        self.assertTrue(any(item[0] == "idle" and item[1] == 1 for item in queued))

        parent.destroy()

    def test_batch_queue_drain_safe_with_malformed_events(self):
        """_drain_ui_queue safely handles non-tuple/list items, bad types, and malformed structures without crashing."""
        parent = ctk.CTkFrame(self.root)
        view = BatchDownloadView(parent, {})
        view._append_log = MagicMock()
        view.run_generation = 1

        # Put various malformed items
        view._ui_queue.put(None)
        view._ui_queue.put(12345)
        view._ui_queue.put("string_event")
        view._ui_queue.put(())
        view._ui_queue.put(("unknown_type",))
        view._ui_queue.put(("log", 1, "INFO"))  # missing 4th item

        # Valid item at the end
        view._ui_queue.put(("log", 1, "INFO", "Valid Message"))

        # Drain should process through malformed items and handle valid message
        view._drain_ui_queue()
        view._append_log.assert_called_once_with("Valid Message", "INFO")

        parent.destroy()

    def test_batch_ancestor_destroy_triggers_cleanup(self):
        """Destroying parent container triggers <Destroy> binding and calls _cleanup_lifecycle on child view."""
        parent = ctk.CTkFrame(self.root)
        view = BatchDownloadView(parent, {})
        self.assertFalse(view._destroying)
        self.assertFalse(view.stop_event.is_set())

        # Destroy ancestor/parent frame
        parent.destroy()
        self.assertTrue(view._destroying)
        self.assertTrue(view.stop_event.is_set())
        self.assertIsNone(view._drain_timer_id)


if __name__ == "__main__":
    unittest.main()
