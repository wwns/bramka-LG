import csv
import threading
import time
import customtkinter as ctk
from tkinter import ttk, messagebox, filedialog
from pymodbus.client import ModbusTcpClient

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# Kody błędów (kod błędu 0x00-0xFF) — używane zarówno dla rejestru statusu
# ogólnego urządzenia (adres 0, bity 8-15), jak i rejestru błędu jednostki ER (offset+6)
ERROR_CODES = {
    0x00: "BRAK_BŁĘDU",
    0x01: "BŁĄD_CRC_LG",
    0x02: "BRAK_ODPOWIEDZI_LG",
    0x03: "ZŁA_TMP_ZADANA",
    0x04: "ZŁA_TMP_ZMIERZONA",
    0x06: "ZŁY_PARAMETR_FAN",
    0x07: "ZŁY_PARAMETR_MODE",
}


def decode_unit_type(r1):
    """Odczytuje flagi AC (bit 10) i VENT (bit 9) z rejestru 1 (typ jednostki)."""
    ac = bool(r1 & (1 << 10))
    vent = bool(r1 & (1 << 9))
    if ac and vent:
        return "AC+VENT", ac, vent
    if ac:
        return "AC", ac, vent
    if vent:
        return "VENT", ac, vent
    return "—", ac, vent


def decode_unit_flags(r5):
    """Odczytuje flagi jednostki z rejestru 5: FA(12), PF(9), PL(6), AS(3), ON(0)."""
    fa = bool(r5 & (1 << 12))
    pf = bool(r5 & (1 << 9))
    pl = bool(r5 & (1 << 6))
    as_ = bool(r5 & (1 << 3))
    on = bool(r5 & 1)
    return fa, pf, pl, as_, on


def flags_text(fa, pf, pl, as_):
    """Buduje krótki tekst z aktywnych flag FA/PF/PL/AS (np. 'PF,AS' lub '-')."""
    active = []
    if fa:
        active.append("FA")
    if pf:
        active.append("PF")
    if pl:
        active.append("PL")
    if as_:
        active.append("AS")
    return ",".join(active) if active else "-"


def error_text(code):
    return f"{hex(code)} ({ERROR_CODES.get(code, 'NIEZNANY')})"


class ModernModbusCSVApp(ctk.CTk):

    def __init__(self):
        super().__init__()

        self.title("ACMC Gateway Controller & CSV Export — Modbus TCP")
        self.geometry("980x1080")
        self.resizable(False, False)

        self.polling_active = False
        self.is_busy_with_action = False

        self._setup_tree_styles()

        # --- SEKCJA 1: POŁĄCZENIE I AUTO-REFRESH ---
        self.frame_conn = ctk.CTkFrame(self)
        self.frame_conn.pack(fill="x", padx=15, pady=6)

        ctk.CTkLabel(
            self.frame_conn,
            text="Połączenie i Konfiguracja Odświeżania",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=10, pady=4)

        self.subframe_conn = ctk.CTkFrame(self.frame_conn, fg_color="transparent")
        self.subframe_conn.pack(fill="x", padx=10, pady=4)

        ctk.CTkLabel(self.subframe_conn, text="IP:").grid(row=0, column=0, padx=5, sticky="w")
        self.ent_ip = ctk.CTkEntry(self.subframe_conn, width=120)
        self.ent_ip.insert(0, "192.168.1.100")
        self.ent_ip.grid(row=0, column=1, padx=5)

        ctk.CTkLabel(self.subframe_conn, text="Port:").grid(row=0, column=2, padx=5, sticky="w")
        self.ent_port = ctk.CTkEntry(self.subframe_conn, width=60)
        self.ent_port.insert(0, "502")
        self.ent_port.grid(row=0, column=3, padx=5)

        unit_options = [f"Jednostka {i} (Reg. {1 + (i-1)*7}–{i*7})" for i in range(1, 251)]
        self.combo_unit = ctk.CTkOptionMenu(self.subframe_conn, values=unit_options, width=260)
        self.combo_unit.set(unit_options[0])
        self.combo_unit.grid(row=0, column=4, padx=10)

        self.switch_poll = ctk.CTkSwitch(
            self.frame_conn,
            text="Auto-odświeżanie wybranej jednostki (co 2s)",
            command=self._toggle_polling,
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.switch_poll.pack(anchor="w", padx=15, pady=6)

        self.lbl_gateway_status = ctk.CTkLabel(
            self.frame_conn,
            text="Status Bramki: Nieaktywny",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        )
        self.lbl_gateway_status.pack(anchor="w", padx=15, pady=2)

        # --- SEKCJA 2: TABELA I PRZYCISKI SKANERA / CSV ---
        self.frame_scan = ctk.CTkFrame(self)
        self.frame_scan.pack(fill="x", padx=15, pady=6)

        ctk.CTkLabel(
            self.frame_scan,
            text="Lista Wykrytych Jednostek",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=10, pady=4)

        self.subframe_scan_btns = ctk.CTkFrame(self.frame_scan, fg_color="transparent")
        self.subframe_scan_btns.pack(fill="x", padx=10, pady=4)
        self.subframe_scan_btns.grid_columnconfigure((0, 1), weight=1)

        self.btn_scan = ctk.CTkButton(
            self.subframe_scan_btns,
            text="SKANUJ SYSTEM (250 JEDNOSTEK NA RAZ)",
            command=self.start_scan,
            fg_color="#1f538d",
            hover_color="#14375e",
        )
        self.btn_scan.grid(row=0, column=0, padx=5, sticky="ew")

        self.btn_export = ctk.CTkButton(
            self.subframe_scan_btns,
            text="EKSPORTUJ DO CSV",
            command=self.export_to_csv,
            fg_color="#D97706",
            hover_color="#B45309",
        )
        self.btn_export.grid(row=0, column=1, padx=5, sticky="ew")

        self.tree_frame = ctk.CTkFrame(self.frame_scan, fg_color="transparent")
        self.tree_frame.pack(fill="x", padx=10, pady=4)

        columns = ("unit", "reg", "lg_addr", "typ", "pwr", "mode", "fan", "set_t", "act_t", "flags", "err")
        self.tree = ttk.Treeview(self.tree_frame, columns=columns, show="headings", height=6)

        self.tree.heading("unit", text="Jednostka")
        self.tree.heading("reg", text="Rejestry")
        self.tree.heading("lg_addr", text="Adres LG")
        self.tree.heading("typ", text="Typ")
        self.tree.heading("pwr", text="Zasilanie")
        self.tree.heading("mode", text="Tryb")
        self.tree.heading("fan", text="Nawiew")
        self.tree.heading("set_t", text="Temp. Zad.")
        self.tree.heading("act_t", text="Temp. Zmier.")
        self.tree.heading("flags", text="Flagi (FA/PF/PL/AS)")
        self.tree.heading("err", text="Błąd Jednostki")

        self.tree.column("unit", width=70, anchor="center")
        self.tree.column("reg", width=80, anchor="center")
        self.tree.column("lg_addr", width=70, anchor="center")
        self.tree.column("typ", width=70, anchor="center")
        self.tree.column("pwr", width=80, anchor="center")
        self.tree.column("mode", width=80, anchor="center")
        self.tree.column("fan", width=70, anchor="center")
        self.tree.column("set_t", width=80, anchor="center")
        self.tree.column("act_t", width=80, anchor="center")
        self.tree.column("flags", width=130, anchor="center")
        self.tree.column("err", width=140, anchor="center")

        vscrollbar = ttk.Scrollbar(self.tree_frame, orient="vertical", command=self.tree.yview)
        hscrollbar = ttk.Scrollbar(self.tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscroll=vscrollbar.set, xscroll=hscrollbar.set)

        hscrollbar.pack(side="bottom", fill="x")
        self.tree.pack(side="left", fill="x", expand=True)
        vscrollbar.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # --- SEKCJA 3: STEROWANIE POJEDYNCZE ---
        self.frame_ctrl = ctk.CTkFrame(self)
        self.frame_ctrl.pack(fill="x", padx=15, pady=6)

        ctk.CTkLabel(
            self.frame_ctrl,
            text="Sterowanie Wybraną Jednostką",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=10, pady=4)

        self.subframe_ctrl_top = ctk.CTkFrame(self.frame_ctrl, fg_color="transparent")
        self.subframe_ctrl_top.pack(fill="x", padx=10, pady=2)

        self.switch_pwr = ctk.CTkSwitch(self.subframe_ctrl_top, text="Zasilanie (OFF / ON)")
        self.switch_pwr.pack(side="left", padx=5)

        self.lbl_slider_val = ctk.CTkLabel(
            self.subframe_ctrl_top,
            text="Temperatura: 22 °C",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.lbl_slider_val.pack(side="right", padx=15)

        self.slider_temp = ctk.CTkSlider(
            self.frame_ctrl,
            from_=18,
            to=30,
            number_of_steps=12,
            command=self._update_slider_label,
        )
        self.slider_temp.set(22)
        self.slider_temp.pack(fill="x", padx=15, pady=4)

        self.subframe_dropdowns = ctk.CTkFrame(self.frame_ctrl, fg_color="transparent")
        self.subframe_dropdowns.pack(fill="x", padx=10, pady=4)

        ctk.CTkLabel(self.subframe_dropdowns, text="Tryb:").grid(row=0, column=0, padx=5, sticky="w")
        self.combo_mode = ctk.CTkOptionMenu(
            self.subframe_dropdowns,
            values=["COOLING (1)", "FAN (2)", "HEAT (3)", "DRY (4)", "AUTO (0)"],
            width=140,
        )
        self.combo_mode.grid(row=0, column=1, padx=5)

        ctk.CTkLabel(self.subframe_dropdowns, text="Nawiew:").grid(row=0, column=2, padx=5, sticky="w")
        self.combo_fan = ctk.CTkOptionMenu(
            self.subframe_dropdowns,
            values=["AUTO (0)", "LOW (2)", "MID (3)", "HIGH (4)", "V_LOW (1)", "V_HIGH (5)"],
            width=140,
        )
        self.combo_fan.grid(row=0, column=3, padx=5)

        # Dodatkowe flagi jednostki: PF (Plasma), PL (Panel Lock), AS (Auto Swing)
        self.subframe_flags = ctk.CTkFrame(self.frame_ctrl, fg_color="transparent")
        self.subframe_flags.pack(fill="x", padx=10, pady=2)

        self.switch_plasma = ctk.CTkSwitch(self.subframe_flags, text="Plazma (PF)")
        self.switch_plasma.pack(side="left", padx=5)

        self.switch_panel_lock = ctk.CTkSwitch(self.subframe_flags, text="Blokada panelu (PL)")
        self.switch_panel_lock.pack(side="left", padx=15)

        self.switch_auto_swing = ctk.CTkSwitch(self.subframe_flags, text="Auto Swing (AS)")
        self.switch_auto_swing.pack(side="left", padx=15)

        # Informacje odczytywane z jednostki (tylko do odczytu)
        self.lbl_unit_info = ctk.CTkLabel(
            self.frame_ctrl,
            text="Typ: — | Alarm filtra (FA): — | Błąd jednostki (ER): —",
            font=ctk.CTkFont(size=11),
            text_color="gray",
        )
        self.lbl_unit_info.pack(anchor="w", padx=15, pady=2)

        self.btn_write = ctk.CTkButton(
            self.frame_ctrl,
            text="WYŚLIJ NASTAWY DO WYBRANEJ JEDNOSTKI",
            command=self.write_data,
            fg_color="#2FA572",
            hover_color="#1E714C",
        )
        self.btn_write.pack(fill="x", padx=10, pady=8)

        # --- SEKCJA 4: MASOWE STEROWANIE ---
        self.frame_bulk = ctk.CTkFrame(self)
        self.frame_bulk.pack(fill="x", padx=15, pady=6)

        ctk.CTkLabel(
            self.frame_bulk,
            text="Masowe Sterowanie Wszystkimi Jednostkami (1–250)",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=10, pady=4)

        # Linia zasilania i suwaka temperatury dla masowego sterowania
        self.subframe_bulk_top = ctk.CTkFrame(self.frame_bulk, fg_color="transparent")
        self.subframe_bulk_top.pack(fill="x", padx=10, pady=2)

        self.switch_bulk_pwr = ctk.CTkSwitch(self.subframe_bulk_top, text="Zasilanie Wszystkich (OFF / ON)")
        self.switch_bulk_pwr.select()
        self.switch_bulk_pwr.pack(side="left", padx=5)

        self.lbl_bulk_slider_val = ctk.CTkLabel(
            self.subframe_bulk_top,
            text="Temperatura: 22 °C",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.lbl_bulk_slider_val.pack(side="right", padx=15)

        self.slider_bulk_temp = ctk.CTkSlider(
            self.frame_bulk,
            from_=18,
            to=30,
            number_of_steps=12,
            command=self._update_bulk_slider_label,
        )
        self.slider_bulk_temp.set(22)
        self.slider_bulk_temp.pack(fill="x", padx=15, pady=4)

        # Dropdowny trybu i nawiewu dla masowego sterowania
        self.subframe_bulk_dropdowns = ctk.CTkFrame(self.frame_bulk, fg_color="transparent")
        self.subframe_bulk_dropdowns.pack(fill="x", padx=10, pady=4)

        ctk.CTkLabel(self.subframe_bulk_dropdowns, text="Tryb:").grid(row=0, column=0, padx=5, sticky="w")
        self.combo_bulk_mode = ctk.CTkOptionMenu(
            self.subframe_bulk_dropdowns,
            values=["COOLING (1)", "FAN (2)", "HEAT (3)", "DRY (4)", "AUTO (0)"],
            width=140,
        )
        self.combo_bulk_mode.grid(row=0, column=1, padx=5)

        ctk.CTkLabel(self.subframe_bulk_dropdowns, text="Nawiew:").grid(row=0, column=2, padx=5, sticky="w")
        self.combo_bulk_fan = ctk.CTkOptionMenu(
            self.subframe_bulk_dropdowns,
            values=["AUTO (0)", "LOW (2)", "MID (3)", "HIGH (4)", "V_LOW (1)", "V_HIGH (5)"],
            width=140,
        )
        self.combo_bulk_fan.grid(row=0, column=3, padx=5)

        # Dodatkowe flagi dla masowego sterowania: PF (Plasma), PL (Panel Lock), AS (Auto Swing)
        self.subframe_bulk_flags = ctk.CTkFrame(self.frame_bulk, fg_color="transparent")
        self.subframe_bulk_flags.pack(fill="x", padx=10, pady=2)

        self.switch_bulk_plasma = ctk.CTkSwitch(self.subframe_bulk_flags, text="Plazma (PF)")
        self.switch_bulk_plasma.pack(side="left", padx=5)

        self.switch_bulk_panel_lock = ctk.CTkSwitch(self.subframe_bulk_flags, text="Blokada panelu (PL)")
        self.switch_bulk_panel_lock.pack(side="left", padx=15)

        self.switch_bulk_auto_swing = ctk.CTkSwitch(self.subframe_bulk_flags, text="Auto Swing (AS)")
        self.switch_bulk_auto_swing.pack(side="left", padx=15)

        # Przyciski akcji zbiorczych
        self.subframe_bulk_btns = ctk.CTkFrame(self.frame_bulk, fg_color="transparent")
        self.subframe_bulk_btns.pack(fill="x", padx=10, pady=6)
        self.subframe_bulk_btns.grid_columnconfigure((0, 1), weight=1)

        self.btn_bulk_off = ctk.CTkButton(
            self.subframe_bulk_btns,
            text="SZYBKIE WYŁĄCZENIE WSZYSTKICH",
            fg_color="#D32F2F",
            hover_color="#9A0007",
            command=lambda: self.start_bulk(power=0, temp=22, mode=1, fan=0),
        )
        self.btn_bulk_off.grid(row=0, column=0, padx=5, sticky="ew")

        self.btn_bulk_apply = ctk.CTkButton(
            self.subframe_bulk_btns,
            text="WYŚLIJ NASTAWY DO WSZYSTKICH JEDNOSTEK",
            fg_color="#1976D2",
            hover_color="#004BA0",
            command=self._apply_bulk_from_gui,
        )
        self.btn_bulk_apply.grid(row=0, column=1, padx=5, sticky="ew")

        self.progress_bar = ctk.CTkProgressBar(self.frame_bulk)
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", padx=10, pady=6)

    def _setup_tree_styles(self):
        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", background="#2a2d2e", foreground="white", fieldbackground="#2a2d2e", rowheight=25)
        style.map("Treeview", background=[("selected", "#1f538d")])
        style.configure("Treeview.Heading", background="#1f1f1f", foreground="white", relief="flat")

    def _update_slider_label(self, value):
        self.lbl_slider_val.configure(text=f"Temperatura: {int(value)} °C")

    def _update_bulk_slider_label(self, value):
        self.lbl_bulk_slider_val.configure(text=f"Temperatura: {int(value)} °C")

    def _get_unit_index(self):
        val = self.combo_unit.get()
        idx = int(val.split(" ")[1])
        return idx, 1 + (idx - 1) * 7

    def _on_tree_select(self, event):
        selected_items = self.tree.selection()
        if not selected_items:
            return
        item = self.tree.item(selected_items[0])
        unit_num = item["values"][0]
        unit_str = f"Jednostka {unit_num} (Reg. {1 + (unit_num-1)*7}–{unit_num*7})"
        self.combo_unit.set(unit_str)

    def export_to_csv(self):
        rows = self.tree.get_children()
        if not rows:
            messagebox.showwarning("Brak danych", "Tabela jest pusta! Przeprowadź skanowanie przed eksportem.")
            return

        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("Pliki CSV", "*.csv"), ("Wszystkie pliki", "*.*")],
            title="Zapisz wyniki skanowania jako CSV",
        )

        if not file_path:
            return

        try:
            with open(file_path, mode="w", newline="", encoding="utf-8-sig") as file:
                writer = csv.writer(file, delimiter=";")
                headers = [
                    "Jednostka",
                    "Zakres Rejestrów",
                    "Adres LG (Grupa-Jednostka)",
                    "Typ",
                    "Zasilanie",
                    "Tryb Pracy",
                    "Nawiew",
                    "Temp. Zadana",
                    "Temp. Zmierzona",
                    "Flagi (FA/PF/PL/AS)",
                    "Błąd Jednostki",
                ]
                writer.writerow(headers)

                for row_id in rows:
                    values = self.tree.item(row_id)["values"]
                    writer.writerow(values)

            messagebox.showinfo("Sukces", f"Pomyślnie wyeksportowano dane do:\n{file_path}")
        except Exception as e:
            messagebox.showerror("Błąd Zapisu", f"Nie udało się zapisać pliku:\n{e}")

    def _toggle_polling(self):
        if self.switch_poll.get() == 1:
            self.polling_active = True
            threading.Thread(target=self._polling_loop, daemon=True).start()
        else:
            self.polling_active = False

    def _polling_loop(self):
        mode_map = {0: "AUTO (0)", 1: "COOLING (1)", 2: "FAN (2)", 3: "HEAT (3)", 4: "DRY (4)"}
        fan_map = {0: "AUTO (0)", 1: "V_LOW (1)", 2: "LOW (2)", 3: "MID (3)", 4: "HIGH (4)", 5: "V_HIGH (5)"}

        while self.polling_active:
            if not self.is_busy_with_action:
                ip = self.ent_ip.get().strip()
                port = int(self.ent_port.get().strip())
                unit_idx, start_reg = self._get_unit_index()

                client = ModbusTcpClient(ip, port=port)
                if client.connect():
                    try:
                        res0 = client.read_input_registers(address=0, count=1, slave=255)
                        if not res0.isError():
                            r0 = res0.registers[0]
                            rdy = bool(r0 & 0x01)
                            scn = bool(r0 & 0x40)
                            bsy = bool(r0 & 0x08)
                            err = (r0 >> 8) & 0xFF
                            status_txt = (
                                f"Status Bramki | RDY: {'TAK' if rdy else 'NIE'} "
                                f"| SCN: {'TAK' if scn else 'NIE'} "
                                f"| BSY: {'TAK' if bsy else 'NIE'} "
                                f"| Błąd: {error_text(err)}"
                            )
                            self.after(0, lambda t=status_txt: self.lbl_gateway_status.configure(text=t, text_color="#2FA572" if rdy else "orange"))

                        res = client.read_input_registers(address=start_reg, count=7, slave=255)
                        if not res.isError():
                            r1, r2, r3, r4, r5, r6, r7 = res.registers
                            typ_txt, ac, vent = decode_unit_type(r1)
                            fa, pf, pl, as_, is_on = decode_unit_flags(r5)
                            mode_idx = r3 & 0xFF
                            fan_idx = r2 & 0xFF
                            set_t = r4 & 0xFF
                            act_t = r6 & 0xFF
                            err_code = r7 & 0xFF

                            self.after(0, self._update_controls_from_poll, is_on, set_t, mode_idx, fan_idx, mode_map, fan_map, pf, pl, as_, typ_txt, fa, err_code)
                            self.after(
                                0,
                                self._update_tree_row_if_exists,
                                unit_idx, typ_txt, is_on,
                                mode_map.get(mode_idx, str(mode_idx)).split(" ")[0],
                                fan_map.get(fan_idx, str(fan_idx)).split(" ")[0],
                                set_t, act_t, flags_text(fa, pf, pl, as_), error_text(err_code),
                            )

                    finally:
                        client.close()

            time.sleep(2.0)

    def _update_controls_from_poll(self, is_on, set_t, mode_idx, fan_idx, mode_map, fan_map, pf, pl, as_, typ_txt, fa, err_code):
        self.switch_pwr.select() if is_on else self.switch_pwr.deselect()
        if 18 <= set_t <= 30:
            self.slider_temp.set(set_t)
            self._update_slider_label(set_t)
        if mode_idx in mode_map:
            self.combo_mode.set(mode_map[mode_idx])
        if fan_idx in fan_map:
            self.combo_fan.set(fan_map[fan_idx])

        self.switch_plasma.select() if pf else self.switch_plasma.deselect()
        self.switch_panel_lock.select() if pl else self.switch_panel_lock.deselect()
        self.switch_auto_swing.select() if as_ else self.switch_auto_swing.deselect()

        fa_txt = "TAK" if fa else "NIE"
        self.lbl_unit_info.configure(
            text=f"Typ: {typ_txt} | Alarm filtra (FA): {fa_txt} | Błąd jednostki (ER): {error_text(err_code)}"
        )

    def _update_tree_row_if_exists(self, unit_idx, typ_txt, is_on, mode_str, fan_str, set_t, act_t, flags_str, err_str):
        for item_id in self.tree.get_children():
            vals = self.tree.item(item_id)["values"]
            if vals[0] == unit_idx:
                new_vals = list(vals)
                new_vals[3] = typ_txt
                new_vals[4] = "ON" if is_on else "OFF"
                new_vals[5] = mode_str
                new_vals[6] = fan_str
                new_vals[7] = f"{set_t} °C"
                new_vals[8] = f"{act_t} °C"
                new_vals[9] = flags_str
                new_vals[10] = err_str
                self.tree.item(item_id, values=new_vals)
                break

    def start_scan(self):
        self.is_busy_with_action = True
        self.btn_scan.configure(state="disabled")
        for row in self.tree.get_children():
            self.tree.delete(row)

        threading.Thread(target=self._scan_thread, daemon=True).start()

    def _scan_thread(self):
        ip = self.ent_ip.get().strip()
        port = int(self.ent_port.get().strip())
        client = ModbusTcpClient(ip, port=port)

        if not client.connect():
            self.after(0, lambda: messagebox.showerror("Błąd", f"Nie połączono z {ip}:{port}"))
            self._finish_action()
            return

        mode_map = {0: "AUTO", 1: "COOL", 2: "FAN", 3: "HEAT", 4: "DRY"}
        fan_map = {0: "AUTO", 1: "V_LOW", 2: "LOW", 3: "MID", 4: "HIGH", 5: "V_HIGH"}

        try:
            all_registers = {}
            total_registers = 1750
            chunk_size = 125

            for start in range(1, total_registers + 1, chunk_size):
                count = min(chunk_size, total_registers - start + 1)
                res = client.read_input_registers(address=start, count=count, slave=255)
                if not res.isError():
                    for idx, val in enumerate(res.registers):
                        all_registers[start + idx] = val
                else:
                    for u in range((start - 1) // 7 + 1, (start + count - 1) // 7 + 1):
                        u_start = 1 + (u - 1) * 7
                        u_res = client.read_input_registers(address=u_start, count=7, slave=255)
                        if not u_res.isError():
                            for idx, val in enumerate(u_res.registers):
                                all_registers[u_start + idx] = val

            rows_to_insert = []
            for i in range(1, 251):
                start_reg = 1 + (i - 1) * 7
                if all(reg in all_registers for reg in range(start_reg, start_reg + 7)):
                    r1 = all_registers[start_reg]
                    r2 = all_registers[start_reg + 1]
                    r3 = all_registers[start_reg + 2]
                    r4 = all_registers[start_reg + 3]
                    r5 = all_registers[start_reg + 4]
                    r6 = all_registers[start_reg + 5]
                    r7 = all_registers[start_reg + 6]

                    if bool(r1 & (1 << 15)) or (r1 != 0):
                        lg_grp = (r1 >> 4) & 0x0F
                        lg_unit = r1 & 0x0F
                        typ_txt, _, _ = decode_unit_type(r1)
                        fa, pf, pl, as_, is_on = decode_unit_flags(r5)
                        row_data = (
                            i,
                            f"{start_reg}-{start_reg+6}",
                            f"{lg_grp}-{lg_unit}",
                            typ_txt,
                            "ON" if is_on else "OFF",
                            mode_map.get(r3 & 0xFF, str(r3)),
                            fan_map.get(r2 & 0xFF, str(r2)),
                            f"{r4 & 0xFF} °C",
                            f"{r6 & 0xFF} °C",
                            flags_text(fa, pf, pl, as_),
                            error_text(r7 & 0xFF),
                        )
                        rows_to_insert.append(row_data)

            self.after(0, lambda: [self.tree.insert("", "end", values=r) for r in rows_to_insert])

        finally:
            client.close()
            self._finish_action()

    def _finish_action(self):
        self.after(0, lambda: self.btn_scan.configure(state="normal"))
        self.after(0, lambda: self.btn_bulk_off.configure(state="normal"))
        self.after(0, lambda: self.btn_bulk_apply.configure(state="normal"))
        self.is_busy_with_action = False

    def _check_gateway_ready(self, client):
        """Sprawdza flagę RDY (bit 0) i BSY (bit 3) rejestru statusu ogólnego (adres 0)
        zgodnie z wymogiem specyfikacji: przed każdym zapisem rejestrów Modbus należy
        bezwzględnie sprawdzić flagę gotowości urządzenia."""
        res0 = client.read_input_registers(address=0, count=1, slave=255)
        if res0.isError():
            return False, "Nie udało się odczytać rejestru statusu bramki (adres 0)."
        r0 = res0.registers[0]
        rdy = bool(r0 & 0x01)
        bsy = bool(r0 & 0x08)
        if bsy:
            return False, "Bramka jest zajęta (BSY) — trwa zapis innej jednostki. Spróbuj ponownie za chwilę."
        if not rdy:
            return False, "Bramka nie jest gotowa do zapisu (RDY=NIE)."
        return True, ""

    def write_data(self):
        self.is_busy_with_action = True
        ip = self.ent_ip.get().strip()
        port = int(self.ent_port.get().strip())
        unit_idx, start_reg = self._get_unit_index()

        client = ModbusTcpClient(ip, port=port)
        if not client.connect():
            messagebox.showerror("Błąd", f"Nie połączono z {ip}:{port}")
            self.is_busy_with_action = False
            return

        try:
            ready, err_msg = self._check_gateway_ready(client)
            if not ready:
                messagebox.showerror("Bramka nie gotowa", err_msg)
                return

            pwr_val = 1 if self.switch_pwr.get() == 1 else 0
            temp_val = int(self.slider_temp.get())
            mode_val = int(self.combo_mode.get().split("(")[1].replace(")", ""))
            fan_val = int(self.combo_fan.get().split("(")[1].replace(")", ""))
            pf_val = 1 if self.switch_plasma.get() == 1 else 0
            pl_val = 1 if self.switch_panel_lock.get() == 1 else 0
            as_val = 1 if self.switch_auto_swing.get() == 1 else 0

            # Rejestr 5 (offset+4): PF(bit9), PL(bit6), AS(bit3), ON(bit0)
            reg5 = (pf_val << 9) | (pl_val << 6) | (as_val << 3) | pwr_val
            payload = [0, fan_val, mode_val, temp_val, reg5, 0, 0]
            res = client.write_registers(address=start_reg, values=payload, slave=255)

            if res.isError():
                messagebox.showerror("Błąd Zapisu", "Bramka odrzuciła zapis.")
            else:
                messagebox.showinfo("Sukces", f"Zapisano ustawienia dla Jednostki {unit_idx}!")
        finally:
            client.close()
            self.is_busy_with_action = False

    def _apply_bulk_from_gui(self):
        pwr_val = 1 if self.switch_bulk_pwr.get() == 1 else 0
        temp_val = int(self.slider_bulk_temp.get())
        mode_val = int(self.combo_bulk_mode.get().split("(")[1].replace(")", ""))
        fan_val = int(self.combo_bulk_fan.get().split("(")[1].replace(")", ""))
        pf_val = 1 if self.switch_bulk_plasma.get() == 1 else 0
        pl_val = 1 if self.switch_bulk_panel_lock.get() == 1 else 0
        as_val = 1 if self.switch_bulk_auto_swing.get() == 1 else 0

        self.start_bulk(power=pwr_val, temp=temp_val, mode=mode_val, fan=fan_val, pf=pf_val, pl=pl_val, aswing=as_val)

    def start_bulk(self, power, temp, mode, fan, pf=0, pl=0, aswing=0):
        pwr_str = "ON" if power == 1 else "OFF"
        msg = (
            f"Czy na pewno chcesz wysłać poniższe ustawienia do 250 jednostek?\n\n"
            f"• Zasilanie: {pwr_str}\n• Temp: {temp} °C\n• Tryb: {mode}\n• Nawiew: {fan}\n"
            f"• Plazma (PF): {'TAK' if pf else 'NIE'}\n"
            f"• Blokada panelu (PL): {'TAK' if pl else 'NIE'}\n"
            f"• Auto Swing (AS): {'TAK' if aswing else 'NIE'}"
        )

        if not messagebox.askyesno("Potwierdzenie Masowej Wysyłki", msg):
            return

        self.is_busy_with_action = True
        self.btn_bulk_off.configure(state="disabled")
        self.btn_bulk_apply.configure(state="disabled")

        threading.Thread(target=self._bulk_thread, args=(250, power, temp, mode, fan, pf, pl, aswing), daemon=True).start()

    def _bulk_thread(self, total, power, temp, mode, fan, pf=0, pl=0, aswing=0):
        ip = self.ent_ip.get().strip()
        port = int(self.ent_port.get().strip())
        client = ModbusTcpClient(ip, port=port)

        if not client.connect():
            self._finish_action()
            return

        try:
            ready, err_msg = self._check_gateway_ready(client)
            if not ready:
                self.after(0, lambda: messagebox.showerror("Bramka nie gotowa", err_msg))
                return

            reg5 = (pf << 9) | (pl << 6) | (aswing << 3) | power
            for i in range(1, total + 1):
                start_reg = 1 + (i - 1) * 7
                payload = [0, fan, mode, temp, reg5, 0, 0]
                client.write_registers(address=start_reg, values=payload, slave=255)

                prog = i / float(total)
                self.after(0, lambda p=prog: self.progress_bar.set(p))
                time.sleep(0.015)
        finally:
            client.close()
            self._finish_action()


if __name__ == "__main__":
    app = ModernModbusCSVApp()
    app.mainloop()