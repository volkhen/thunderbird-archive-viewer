import sys, os, sqlite3, hashlib, html, mmap, tempfile, shutil, re
from email import policy
from email.parser import BytesParser
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from PySide6.QtCore import Qt, QThread, Signal, QAbstractTableModel, QModelIndex, QSortFilterProxyModel, QSettings, QUrl
from PySide6.QtWidgets import (QApplication,QMainWindow,QSplitter,QTreeWidget,QTreeWidgetItem,QTableView,
 QTextBrowser,QToolBar,QFileDialog,QLineEdit,QLabel,QStatusBar,QHeaderView,QMessageBox,QProgressBar,QProgressDialog,
 QDialog,QVBoxLayout,QRadioButton,QDialogButtonBox,QGroupBox,QHBoxLayout,QPushButton,QWidget,QScrollArea,QComboBox)
from PySide6.QtGui import QAction, QPalette, QColor, QDesktopServices, QPixmap

APP_VERSION='0.8.2'
INDEX_VERSION='4'
PARSER=BytesParser(policy=policy.default)

def dh(v):
    if not v: return ''
    try: return str(make_header(decode_header(str(v))))
    except Exception: return str(v)

def date_values(m):
    raw=dh(m.get('Date'))
    try:
        d=parsedate_to_datetime(str(m.get('Date','')))
        if d:
            return d.strftime('%Y-%m-%d %H:%M'), d.timestamp() if d.tzinfo else d.replace(tzinfo=None).timestamp()
    except Exception: pass
    return raw,0.0

def body_of(m):
    plain=[]; htmls=[]
    for p in (m.walk() if m.is_multipart() else [m]):
        if p.get_content_disposition()=='attachment': continue
        if p.get_content_type() not in ('text/plain','text/html'): continue
        try:
            txt=p.get_content(); txt=txt if isinstance(txt,str) else str(txt)
        except Exception:
            try: txt=(p.get_payload(decode=True) or b'').decode(p.get_content_charset() or 'utf-8',errors='replace')
            except Exception: continue
        (htmls if p.get_content_type()=='text/html' else plain).append(txt)
    if htmls: return ''.join(htmls)
    return '<pre style="white-space:pre-wrap">'+html.escape('\n'.join(plain))+'</pre>'

def attachment_parts(m):
    out=[]
    for p in m.walk():
        fn=p.get_filename(); disp=p.get_content_disposition()
        if disp=='attachment' or (fn and disp!='inline'):
            data=p.get_payload(decode=True) or b''
            out.append((dh(fn) or 'attachment',p.get_content_type(),len(data),data))
    return out

def attachment_meta(m):
    out=[]
    for p in m.walk():
        fn=p.get_filename(); disp=p.get_content_disposition()
        if disp=='attachment' or (fn and disp!='inline'):
            data=p.get_payload(decode=True) or b''
            out.append((dh(fn) or 'attachment',p.get_content_type(),len(data)))
    return out

def folder_display_name(path):
    # v0.8.2: use the physical MBOX filename exactly as stored.
    # .msf metadata is intentionally ignored for folder naming.
    return os.path.basename(path)

def readable_html(content):
    css='<style>html,body{background:#fff !important;color:#000 !important} body,table,td,th,p,div,span,font{color:#000} a{color:#0645ad !important} hr{border-color:#888}</style>'
    low=content.lower()
    hp=low.find('<head')
    if hp>=0:
        i=low.find('>',hp)
        if i>=0:return content[:i+1]+css+content[i+1:]
    return '<html><head>'+css+'</head><body>'+content+'</body></html>'

def is_mbox(path):
    if not os.path.isfile(path): return False
    if os.path.basename(path).lower().endswith(('.msf','.dat','.html','.sqlite','.sqlite3','.json','.ini')): return False
    try:
        if os.path.getsize(path)==0:return False
        with open(path,'rb') as f: head=f.read(65536)
        return head.startswith(b'From ') or b'\nFrom ' in head
    except OSError:return False

def index_dir():
    p=os.path.join(os.environ.get('LOCALAPPDATA',os.path.expanduser('~')),'ThunderbirdArchiveViewer','indexes'); os.makedirs(p,exist_ok=True); return p

def db_path_for(path):
    key=hashlib.sha256(os.path.abspath(path).encode('utf-8','surrogatepass')).hexdigest()[:24]; return os.path.join(index_dir(),key+'.sqlite3')

def read_message(path,offset,length):
    with open(path,'rb') as f: f.seek(offset); raw=f.read(length)
    return PARSER.parsebytes(raw)

class IndexWorker(QThread):
    progress=Signal(int,int,object,int); done=Signal(str,int); failed=Signal(str); canceled=Signal()
    MAX_HEADER_BYTES=512*1024
    def __init__(self,path,dbpath,mode='FAST'): super().__init__(); self.path=path; self.dbpath=dbpath; self.mode=mode
    def run(self):
        tmp=self.dbpath+'.tmp'
        try:
            try:os.remove(tmp)
            except OSError:pass
            size=os.path.getsize(self.path); mtime=os.stat(self.path).st_mtime_ns
            con=sqlite3.connect(tmp); con.execute('PRAGMA journal_mode=OFF'); con.execute('PRAGMA synchronous=OFF'); con.execute('PRAGMA temp_store=MEMORY')
            con.execute('CREATE TABLE meta(k TEXT PRIMARY KEY,v TEXT)')
            con.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY,offset INTEGER,length INTEGER,sender TEXT,recipient TEXT,subject TEXT,date_text TEXT,date_ts REAL,attachments INTEGER)')
            con.execute('CREATE TABLE attachments(message_id INTEGER,filename TEXT,mime TEXT,size INTEGER)')
            count=0; found_att=0; last_emit=0
            with open(self.path,'rb') as f:
                if size==0:raise ValueError('Mailbox is empty.')
                mm=mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ)
                try:
                    if mm[:5]!=b'From ':raise ValueError('This file does not start with an MBOX From_ separator.')
                    first_eol=mm.find(b'\n',0,min(size,1024*1024))
                    if first_eol<0:raise ValueError('Invalid MBOX separator.')
                    msg_start=first_eol+1; search_from=msg_start
                    while True:
                        if self.isInterruptionRequested():
                            con.close();
                            try:os.remove(tmp)
                            except OSError:pass
                            self.canceled.emit();return
                        marker=mm.find(b'\nFrom ',search_from); msg_end=size if marker<0 else marker+1
                        count+=1; found_att+=self.store_message(con,count,msg_start,msg_end-msg_start,mm)
                        pos=msg_end
                        if marker<0:break
                        sep_start=marker+1; sep_eol=mm.find(b'\n',sep_start,min(size,sep_start+1024*1024))
                        if sep_eol<0:break
                        msg_start=sep_eol+1; search_from=msg_start
                        if pos-last_emit>=8*1024*1024:
                            self.progress.emit(min(99,int(pos*100/max(size,1))),count,pos,found_att);last_emit=pos
                        if count%1000==0:con.commit()
                finally:mm.close()
            con.execute('CREATE INDEX idx_date ON messages(date_ts)')
            for k,v in [('path',os.path.abspath(self.path)),('size',str(size)),('mtime_ns',str(mtime)),('version',INDEX_VERSION),('level',self.mode)]: con.execute('INSERT INTO meta VALUES(?,?)',(k,v))
            con.commit();con.close();os.replace(tmp,self.dbpath);self.progress.emit(100,count,size,found_att);self.done.emit(self.dbpath,count)
        except Exception as e:
            try:os.remove(tmp)
            except OSError:pass
            self.failed.emit(str(e))
    def store_message(self,con,n,offset,length,mm):
        sample=mm[offset:min(offset+length,offset+self.MAX_HEADER_BYTES)]
        p1=sample.find(b'\r\n\r\n');p2=sample.find(b'\n\n');ends=[x for x in (p1+4 if p1>=0 else -1,p2+2 if p2>=0 else -1) if x>=0]
        hb=sample[:min(ends)] if ends else sample
        try:
            h=PARSER.parsebytes(hb,headersonly=True);sender=dh(h.get('From'));recipient=dh(h.get('To'));subject=dh(h.get('Subject'));dt,ts=date_values(h)
        except Exception:sender=recipient=subject=dt='';ts=0.
        acount=-1
        if self.mode=='FULL':
            try:
                m=PARSER.parsebytes(mm[offset:offset+length]); meta=attachment_meta(m); acount=len(meta)
                for fn,mime,sz in meta:con.execute('INSERT INTO attachments VALUES(?,?,?,?)',(n,fn,mime,sz))
            except Exception:acount=0
        con.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?)',(n,offset,length,sender,recipient,subject,dt,ts,acount))
        return max(acount,0)

class MessageModel(QAbstractTableModel):
    headers=['From','Subject','Date','Attachments']
    def __init__(self):super().__init__();self.rows=[]
    def set_rows(self,rows):self.beginResetModel();self.rows=rows;self.endResetModel()
    def rowCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(self.rows)
    def columnCount(self,parent=QModelIndex()):return 4
    def headerData(self,s,o,role=Qt.DisplayRole):return self.headers[s] if role==Qt.DisplayRole and o==Qt.Horizontal else None
    def data(self,index,role=Qt.DisplayRole):
        if not index.isValid():return None
        r=self.rows[index.row()]
        if role==Qt.DisplayRole:
            a='?' if r[8]<0 else (str(r[8]) if r[8] else '')
            return (r[3],r[5],r[6],a)[index.column()]
        if role==Qt.UserRole:return r
        return None

class MailFilter(QSortFilterProxyModel):
    def __init__(self):super().__init__();self.term='';self.setDynamicSortFilter(True)
    def set_term(self,t):self.term=t.casefold().strip();self.invalidateFilter()
    def filterAcceptsRow(self,row,parent):
        if not self.term:return True
        m=self.sourceModel();return self.term in ' '.join(str(m.data(m.index(row,c),Qt.DisplayRole) or '') for c in range(3)).casefold()

class SettingsDialog(QDialog):
    def __init__(self,parent,mode):
        super().__init__(parent);self.setWindowTitle('Settings');lay=QVBoxLayout(self);g=QGroupBox('Indexing mode');gl=QVBoxLayout(g)
        self.fast=QRadioButton('Fast indexing (recommended) — headers and message positions only');self.full=QRadioButton('Full indexing — analyze MIME and attachments during indexing')
        (self.full if mode=='FULL' else self.fast).setChecked(True);gl.addWidget(self.fast);gl.addWidget(self.full)
        gl.addWidget(QLabel('Fast mode shows ? until a message is opened. Full mode is slower but attachment information is available immediately.'))
        lay.addWidget(g);bb=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel);bb.accepted.connect(self.accept);bb.rejected.connect(self.reject);lay.addWidget(bb)
    def mode(self):return 'FULL' if self.full.isChecked() else 'FAST'

class Main(QMainWindow):
    def __init__(self):
        super().__init__();self.setWindowTitle('Thunderbird Archive Viewer v'+APP_VERSION);self.resize(1350,850);self.current_path=None;self.current_db=None;self.worker=None;self.progress_dialog=None;self.temp_dir=tempfile.mkdtemp(prefix='TAV_')
        tb=QToolBar();self.addToolBar(tb)
        a=QAction('Open Archive Folder',self);a.triggered.connect(self.open_folder);tb.addAction(a);f=QAction('Open MBOX File',self);f.triggered.connect(self.open_file);tb.addAction(f);tb.addSeparator()
        self.save_eml_action=QAction('Save message as EML…',self);self.save_eml_action.triggered.connect(self.save_message_as_eml);self.save_eml_action.setEnabled(False);tb.addAction(self.save_eml_action);tb.addSeparator()
        file_menu=self.menuBar().addMenu('&File');file_menu.addAction(a);file_menu.addAction(f);file_menu.addSeparator();file_menu.addAction(self.save_eml_action)
        settings=QAction('Settings',self);settings.triggered.connect(self.open_settings);tb.addAction(settings)
        self.theme_action=QAction('Light mode',self);self.theme_action.setCheckable(True);self.theme_action.triggered.connect(self.toggle_theme);tb.addAction(self.theme_action);tb.addSeparator();tb.addWidget(QLabel(' Message: '));self.render_mode=QComboBox();self.render_mode.addItems(['Readable Light','Original']);self.render_mode.currentTextChanged.connect(self.rerender_selected);tb.addWidget(self.render_mode);tb.addSeparator();tb.addWidget(QLabel(' Search: '));self.search=QLineEdit();self.search.setPlaceholderText('Filter current mailbox…');tb.addWidget(self.search)
        spl=QSplitter();right=QSplitter(Qt.Vertical);self.tree=QTreeWidget();self.tree.setHeaderLabel('Folders');self.tree.itemClicked.connect(self.tree_click)
        self.model=MessageModel();self.proxy=MailFilter();self.proxy.setSourceModel(self.model);self.table=QTableView();self.table.setModel(self.proxy);self.table.setSelectionBehavior(QTableView.SelectRows);self.table.setSelectionMode(QTableView.SingleSelection);self.table.setEditTriggers(QTableView.NoEditTriggers);self.table.setSortingEnabled(True);self.table.selectionModel().selectionChanged.connect(self.show_selected);self.search.textChanged.connect(self.proxy.set_term)
        self.table.setContextMenuPolicy(Qt.ActionsContextMenu);self.table.addAction(self.save_eml_action)
        h=self.table.horizontalHeader();h.setSectionResizeMode(0,QHeaderView.ResizeToContents);h.setSectionResizeMode(1,QHeaderView.Stretch);h.setSectionResizeMode(2,QHeaderView.ResizeToContents);h.setSectionResizeMode(3,QHeaderView.ResizeToContents)
        lower=QWidget();ll=QVBoxLayout(lower);ll.setContentsMargins(0,0,0,0);self.view=QTextBrowser();self.view.setOpenExternalLinks(False);ll.addWidget(self.view,1)
        self.attach_box=QGroupBox('Attachments');self.attach_layout=QVBoxLayout(self.attach_box);self.attach_box.hide();ll.addWidget(self.attach_box)
        right.addWidget(self.table);right.addWidget(lower);right.setSizes([360,490]);spl.addWidget(self.tree);spl.addWidget(right);spl.setSizes([260,1090]);self.setCentralWidget(spl)
        st=QStatusBar();self.setStatusBar(st);self.readonly=QLabel('  READ ONLY  ');self.readonly.setToolTip('Source mail archives are opened only in binary read-only mode and are never modified.');st.addPermanentWidget(self.readonly);self.progress=QProgressBar();self.progress.setMaximumWidth(230);self.progress.hide();st.addPermanentWidget(self.progress)
        self.settings=QSettings('MailArchiveTools','ThunderbirdArchiveViewer');self.dark_mode=self.settings.value('dark_mode',True,type=bool);self.index_mode=self.settings.value('index_mode','FAST');self.render_mode.setCurrentText(self.settings.value('render_mode','Readable Light'));self.apply_theme()
    def closeEvent(self,e):
        try:shutil.rmtree(self.temp_dir,ignore_errors=True)
        finally:super().closeEvent(e)
    def open_settings(self):
        d=SettingsDialog(self,self.index_mode)
        if d.exec():
            old=self.index_mode;self.index_mode=d.mode();self.settings.setValue('index_mode',self.index_mode)
            if old!=self.index_mode and self.current_path and self.index_mode=='FULL':
                db=db_path_for(self.current_path)
                if self.index_level(db)=='FAST' and QMessageBox.question(self,'Upgrade index','Upgrade the current mailbox to a Full index now?\n\nThis will rescan it read-only and collect attachment information.')==QMessageBox.Yes:self.rebuild_current()
    def rebuild_current(self):
        if self.current_path:
            try:os.remove(db_path_for(self.current_path))
            except OSError:pass
            self.load(self.current_path)
    def toggle_theme(self):
        self.dark_mode=not self.dark_mode;self.settings.setValue('dark_mode',self.dark_mode);self.apply_theme()
    def rerender_selected(self,*_):
        if hasattr(self,'settings'):self.settings.setValue('render_mode',self.render_mode.currentText())
        if self.table.currentIndex().isValid():self.show_selected()
    def apply_theme(self):
        app=QApplication.instance();app.setStyle('Fusion');pal=QPalette()
        if self.dark_mode:
            colors=[(QPalette.Window,(45,45,45)),(QPalette.WindowText,(235,235,235)),(QPalette.Base,(32,32,32)),(QPalette.AlternateBase,(45,45,45)),(QPalette.Text,(235,235,235)),(QPalette.Button,(45,45,45)),(QPalette.ButtonText,(235,235,235)),(QPalette.Highlight,(0,120,215)),(QPalette.HighlightedText,(255,255,255))]
            self.theme_action.setText('Light mode');self.theme_action.setChecked(False)
        else:
            colors=[(QPalette.Window,(240,240,240)),(QPalette.WindowText,(0,0,0)),(QPalette.Base,(255,255,255)),(QPalette.AlternateBase,(245,245,245)),(QPalette.Text,(0,0,0)),(QPalette.Button,(240,240,240)),(QPalette.ButtonText,(0,0,0)),(QPalette.Highlight,(0,120,215)),(QPalette.HighlightedText,(255,255,255))]
            self.theme_action.setText('Dark mode');self.theme_action.setChecked(True)
        for role,color in colors:pal.setColor(role,QColor(*color))
        app.setPalette(pal)
        self.view.setStyleSheet('QTextBrowser { background: white; color: black; }')
        if self.table.currentIndex().isValid():self.show_selected()
    def open_folder(self):
        p=QFileDialog.getExistingDirectory(self,'Open Thunderbird archive folder');
        if p:self.scan(p)
    def open_file(self):
        p,_=QFileDialog.getOpenFileName(self,'Open MBOX file','','All files (*)')
        if p:self.tree.clear();it=QTreeWidgetItem([folder_display_name(p)]);it.setData(0,Qt.UserRole,p);self.tree.addTopLevelItem(it);self.load(p)
    def scan(self,root):
        self.tree.clear();base=QTreeWidgetItem([os.path.basename(root) or root]);self.tree.addTopLevelItem(base)
        def add_dir(parent,path):
            try:entries=sorted(os.listdir(path),key=str.lower)
            except OSError:return
            for n in entries:
                p=os.path.join(path,n)
                if is_mbox(p):
                    it=QTreeWidgetItem([folder_display_name(p)]);it.setData(0,Qt.UserRole,p);parent.addChild(it)
                    if os.path.isdir(p+'.sbd'):add_dir(it,p+'.sbd')
        add_dir(base,root);base.setExpanded(True);self.statusBar().showMessage('Archive scanned. Original files remain read-only.')
    def tree_click(self,it,col):
        p=it.data(0,Qt.UserRole)
        if p:self.load(p)
    def index_level(self,db):
        try:
            con=sqlite3.connect(db);meta=dict(con.execute('SELECT k,v FROM meta'));con.close();return meta.get('level','FAST')
        except Exception:return None
    def valid_index(self,path,db):
        if not os.path.exists(db):return False
        try:
            con=sqlite3.connect(db);meta=dict(con.execute('SELECT k,v FROM meta'));con.close();s=os.stat(path)
            return meta.get('size')==str(s.st_size) and meta.get('mtime_ns')==str(s.st_mtime_ns) and meta.get('version')==INDEX_VERSION
        except Exception:return False
    def load(self,path):
        if self.worker and self.worker.isRunning():QMessageBox.information(self,'Indexing in progress','Please wait for the current mailbox index to finish.');return
        self.current_path=path;self.model.set_rows([]);self.view.clear();self.clear_attachments();db=db_path_for(path);self.current_db=db
        if self.valid_index(path,db):
            if self.index_mode=='FULL' and self.index_level(db)!='FULL':
                if QMessageBox.question(self,'Full indexing enabled','This mailbox has a Fast index. Upgrade it to Full now?')==QMessageBox.Yes:
                    try:os.remove(db)
                    except OSError:pass
                else:self.load_db(db);return
            else:self.load_db(db);return
        self.progress.setValue(0);self.progress.show();mode=self.index_mode;self.statusBar().showMessage(f'Indexing {os.path.basename(path)} ({mode.lower()})…')
        self.progress_dialog=QProgressDialog('','Cancel',0,100,self);self.progress_dialog.setWindowTitle('Indexing mailbox');self.progress_dialog.setWindowModality(Qt.WindowModal);self.progress_dialog.setMinimumDuration(0);self.progress_dialog.setAutoClose(False);self.progress_dialog.setValue(0)
        self.worker=IndexWorker(path,db,mode);self.worker.progress.connect(self.index_progress);self.worker.done.connect(self.index_done);self.worker.failed.connect(self.index_failed);self.worker.canceled.connect(self.index_canceled);self.progress_dialog.canceled.connect(self.worker.requestInterruption);self.worker.start()
    @staticmethod
    def format_bytes(n):
        n=float(n)
        for unit in ('B','KB','MB','GB','TB'):
            if n<1024 or unit=='TB':return f'{n:.0f} {unit}' if unit in ('B','KB') else f'{n:.2f} {unit}'
            n/=1024
    def index_progress(self,pct,count,bytes_done,atts):
        self.progress.setValue(pct);total=os.path.getsize(self.current_path);name=os.path.basename(self.current_path);extra=f' — {atts:,} attachments' if self.index_mode=='FULL' else ''
        self.statusBar().showMessage(f'Indexing {name} — {pct}% — {count:,} messages{extra}')
        if self.progress_dialog:
            self.progress_dialog.setValue(pct);label=f'Indexing mailbox: {name}\nMode: {self.index_mode.title()}\n\n{self.format_bytes(bytes_done)} / {self.format_bytes(total)}\nMessages found: {count:,}'
            if self.index_mode=='FULL':label+=f'\nAttachments found: {atts:,}'
            self.progress_dialog.setLabelText(label+'\n\nSource archive is read-only.')
    def close_progress_dialog(self):
        if self.progress_dialog:self.progress_dialog.close();self.progress_dialog.deleteLater();self.progress_dialog=None
    def index_done(self,db,count):self.progress.hide();self.close_progress_dialog();self.load_db(db)
    def index_failed(self,msg):self.progress.hide();self.close_progress_dialog();QMessageBox.critical(self,'Cannot index mailbox',msg);self.statusBar().showMessage('Indexing failed.')
    def index_canceled(self):self.progress.hide();self.close_progress_dialog();self.statusBar().showMessage('Indexing canceled. No changes were made to the mailbox.')
    def load_db(self,db):
        try:
            con=sqlite3.connect(db);rows=con.execute('SELECT id,offset,length,sender,recipient,subject,date_text,date_ts,attachments FROM messages ORDER BY id').fetchall();meta=dict(con.execute('SELECT k,v FROM meta'));con.close();self.model.set_rows(rows);self.table.sortByColumn(2,Qt.AscendingOrder);self.statusBar().showMessage(f'{os.path.basename(self.current_path)} — {len(rows):,} messages — {meta.get("level","FAST")} index')
        except Exception as e:QMessageBox.critical(self,'Cannot read index',str(e))
    def selected_record(self):
        idx=self.table.currentIndex()
        if not idx.isValid() or not self.current_path:return None
        src=self.proxy.mapToSource(idx)
        if not src.isValid() or src.row()<0 or src.row()>=len(self.model.rows):return None
        return self.model.rows[src.row()]
    def eml_default_name(self,record):
        subject=(record[5] or 'message').strip()
        bad='<>:\\/|?*\"'
        name=''.join('_' if c in bad or ord(c)<32 else c for c in subject).strip(' .')
        if not name:name='message'
        if len(name)>120:name=name[:120].rstrip(' .')
        return name+'.eml'
    def save_message_as_eml(self):
        r=self.selected_record()
        if not r:return
        suggested=self.eml_default_name(r)
        p,_=QFileDialog.getSaveFileName(self,'Save message as EML',suggested,'Email message (*.eml);;All files (*)')
        if not p:return
        if not os.path.splitext(p)[1]:p+='.eml'
        try:
            # Copy the original RFC/MIME message bytes directly from the read-only MBOX.
            # The MBOX From_ separator is outside the indexed message range and is not exported.
            with open(self.current_path,'rb') as src:
                src.seek(r[1]);remaining=r[2]
                with open(p,'wb') as dst:
                    while remaining:
                        chunk=src.read(min(1024*1024,remaining))
                        if not chunk:break
                        dst.write(chunk);remaining-=len(chunk)
            if remaining:raise IOError('The source message ended unexpectedly while exporting.')
            self.statusBar().showMessage(f'Saved EML: {p}',5000)
        except Exception as e:QMessageBox.critical(self,'Cannot save message',str(e))
    def clear_attachments(self):
        while self.attach_layout.count():
            item=self.attach_layout.takeAt(0);w=item.widget();
            if w:w.deleteLater()
        self.attach_box.hide()
    def show_selected(self,*args):
        idx=self.table.currentIndex()
        self.save_eml_action.setEnabled(bool(idx.isValid() and self.current_path))
        if not idx.isValid() or not self.current_path:return
        src=self.proxy.mapToSource(idx);r=self.model.rows[src.row()]
        try:
            m=read_message(self.current_path,r[1],r[2]);content=body_of(m);head=f'<div style="font-family:Segoe UI;font-size:10pt"><b>{html.escape(dh(m.get("Subject")))}</b><br><br><b>From:</b> {html.escape(dh(m.get("From")))}<br><b>To:</b> {html.escape(dh(m.get("To")))}<br><b>Date:</b> {html.escape(dh(m.get("Date")))}<hr></div>';rendered=readable_html(content) if self.render_mode.currentText()=='Readable Light' else content;self.view.setHtml('<div style=\"background:#fff;color:#000\">'+head+rendered+'</div>');atts=attachment_parts(m);self.show_attachments(atts)
            if r[8]<0:self.update_attachment_count(r[0],len(atts),src.row())
        except Exception as e:self.view.setPlainText('Could not read this message:\n'+str(e));self.clear_attachments()
    def update_attachment_count(self,msg_id,count,row):
        if not self.current_db:return
        try:
            con=sqlite3.connect(self.current_db);con.execute('UPDATE messages SET attachments=? WHERE id=?',(count,msg_id));con.commit();con.close();rr=list(self.model.rows[row]);rr[8]=count;self.model.rows[row]=tuple(rr);self.model.dataChanged.emit(self.model.index(row,3),self.model.index(row,3))
        except Exception:pass
    def show_attachments(self,atts):
        self.clear_attachments()
        if not atts:return
        self.attach_box.show();top=QWidget();hl=QHBoxLayout(top);hl.setContentsMargins(0,0,0,0);hl.addWidget(QLabel(f'{len(atts)} attachment(s)'));hl.addStretch();sa=QPushButton('Save All…');sa.clicked.connect(lambda:self.save_all(atts));hl.addWidget(sa);self.attach_layout.addWidget(top)
        for fn,mime,sz,data in atts:
            row=QWidget();h=QHBoxLayout(row);h.setContentsMargins(0,0,0,0);h.addWidget(QLabel(f'{fn}   ({self.format_bytes(sz)}, {mime})'),1);op=QPushButton('Open');op.clicked.connect(lambda _,a=(fn,data):self.open_attachment(*a));sv=QPushButton('Save As…');sv.clicked.connect(lambda _,a=(fn,data):self.save_attachment(*a));h.addWidget(op);h.addWidget(sv);self.attach_layout.addWidget(row)
            # Lazy inline preview for common image attachments.  Decode only
            # when this message is selected; indexing remains unaffected.
            if mime.lower() in ('image/jpeg','image/jpg','image/png','image/gif','image/bmp','image/webp'):
                pix=QPixmap()
                if pix.loadFromData(data):
                    preview=QLabel();preview.setAlignment(Qt.AlignCenter);preview.setMinimumHeight(80)
                    preview.setToolTip('Image attachment preview — use Open for full size')
                    max_w=max(320,min(900,self.width()-390));max_h=420
                    preview.setPixmap(pix.scaled(max_w,max_h,Qt.KeepAspectRatio,Qt.SmoothTransformation))
                    self.attach_layout.addWidget(preview)
    def safe_name(self,name):return os.path.basename(name).replace('\x00','') or 'attachment'
    def open_attachment(self,name,data):
        try:
            fn=self.safe_name(name);path=os.path.join(self.temp_dir,fn);base,ext=os.path.splitext(path);i=1
            while os.path.exists(path):path=f'{base}_{i}{ext}';i+=1
            with open(path,'wb') as f:f.write(data)
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception as e:QMessageBox.critical(self,'Cannot open attachment',str(e))
    def save_attachment(self,name,data):
        p,_=QFileDialog.getSaveFileName(self,'Save attachment',self.safe_name(name))
        if p:
            try:
                with open(p,'wb') as f:f.write(data)
            except Exception as e:QMessageBox.critical(self,'Cannot save attachment',str(e))
    def save_all(self,atts):
        folder=QFileDialog.getExistingDirectory(self,'Save all attachments')
        if not folder:return
        try:
            for fn,mime,sz,data in atts:
                name=self.safe_name(fn);path=os.path.join(folder,name);base,ext=os.path.splitext(path);i=1
                while os.path.exists(path):path=f'{base}_{i}{ext}';i+=1
                with open(path,'wb') as f:f.write(data)
        except Exception as e:QMessageBox.critical(self,'Cannot save attachments',str(e))

if __name__=='__main__':
    app=QApplication(sys.argv);app.setStyle('Fusion');w=Main();w.show();sys.exit(app.exec())
