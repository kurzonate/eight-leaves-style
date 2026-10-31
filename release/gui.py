"""The installer's window (extract.py does the work)."""
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import extract as X

TITLE = 'Eight Leaves Style - installer'


class App:
    def __init__(self, root):
        self.root = root
        root.title(TITLE)
        root.minsize(640, 420)
        self.events = queue.Queue()
        pad = dict(padx=8, pady=4)
        intro = ('Installs Eight Leaves Style (Shizuna\'s moveset for Estelle) into Trails in the Sky 2nd Chapter.\n'
                 'Its motions, katana and effects are rebuilt from your own copy of Trails beyond the Horizon.')
        ttk.Label(root, text=intro, justify='left').grid(row=0, column=0, columnspan=3, sticky='w', **pad)
        self.horizon = tk.StringVar(value=X.guess('horizon'))
        self.sky = tk.StringVar(value=X.guess('sky'))
        for row, (label, var) in enumerate((('Trails beyond the Horizon folder', self.horizon),
                                            ('Trails in the Sky 2nd Chapter folder', self.sky)), start=1):
            ttk.Label(root, text=label).grid(row=row, column=0, sticky='w', **pad)
            ttk.Entry(root, textvariable=var, width=60).grid(row=row, column=1, sticky='we', **pad)
            ttk.Button(root, text='Browse...', command=lambda v=var: self.browse(v)).grid(row=row, column=2, **pad)
        self.state = ttk.Label(root, text='')
        self.state.grid(row=3, column=0, columnspan=3, sticky='w', **pad)
        buttons = ttk.Frame(root)
        buttons.grid(row=4, column=0, columnspan=3, sticky='w', **pad)
        self.install_btn = ttk.Button(buttons, text='Install', command=self.install)
        self.install_btn.pack(side='left', padx=(0, 8))
        self.uninstall_btn = ttk.Button(buttons, text='Uninstall', command=self.uninstall)
        self.uninstall_btn.pack(side='left')
        self.bar = ttk.Progressbar(root, mode='determinate', maximum=1.0)
        self.bar.grid(row=5, column=0, columnspan=3, sticky='we', **pad)
        self.log_box = tk.Text(root, height=12, state='disabled', wrap='word')
        self.log_box.grid(row=6, column=0, columnspan=3, sticky='nsew', **pad)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(6, weight=1)
        self.sky.trace_add('write', lambda *_: self.refresh())
        self.refresh()
        root.after(100, self.pump)

    def browse(self, var):
        d = filedialog.askdirectory(initialdir=var.get() or None)
        if d:
            var.set(d)

    def refresh(self):
        d = X.sky_archives(self.sky.get()) if self.sky.get() else None
        if not d:
            self.state.config(text='Pick the Trails in the Sky 2nd Chapter folder.')
        else:
            self.state.config(text='Status: installed' if X.installed(d) else 'Status: not installed')

    # work happens on a thread; the window only reads its queue
    def log(self, text):
        self.events.put(('log', text))

    def progress(self, done, total, what):
        self.events.put(('progress', (done / total) if total else 0.0))

    def pump(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'log':
                    self.log_box.config(state='normal')
                    self.log_box.insert('end', value + '\n')
                    self.log_box.see('end')
                    self.log_box.config(state='disabled')
                elif kind == 'progress':
                    self.bar['value'] = value
                elif kind == 'done':
                    self.busy(False)
                    self.refresh()
                    title, text, error = value
                    (messagebox.showerror if error else messagebox.showinfo)(title, text)
                elif kind == 'ask':
                    question, reply = value
                    reply.put(messagebox.askyesno(TITLE, question))
        except queue.Empty:
            pass
        self.root.after(100, self.pump)

    def busy(self, on):
        for b in (self.install_btn, self.uninstall_btn):
            b.config(state='disabled' if on else 'normal')

    def ask(self, question):
        reply = queue.Queue()
        self.events.put(('ask', (question, reply)))
        return reply.get()

    def run(self, job, done_text):
        self.busy(True)

        def work():
            try:
                job()
                self.events.put(('done', (TITLE, done_text, False)))
            except X.Problem as e:
                self.events.put(('done', (TITLE, str(e), True)))
            except Exception as e:   # anything else: report it whole, for a bug report
                import traceback
                self.log(traceback.format_exc())
                self.events.put(('done', (TITLE, f'Unexpected error: {e}\nSee the log for details.', True)))
        threading.Thread(target=work, daemon=True).start()

    def install(self):
        def accept(differ):
            return self.ask(f'{len(differ)} rebuilt files differ from the mod\'s own build (a different game version?).\n'
                            'Install anyway?')
        self.run(lambda: X.install(self.horizon.get(), self.sky.get(), self.log, self.progress, accept),
                 'Installed. Estelle learns Eight Leaves Style at the start of your next battle.')

    def uninstall(self):
        self.run(lambda: X.uninstall(self.sky.get(), self.log), 'Uninstalled. The game\'s original archives are back.')


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == '__main__':
    main()
