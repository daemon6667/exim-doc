#!/bin/env python
# -*- coding: utf-8 -*-

from flask import Flask, request, session, g, redirect, url_for, \
             abort, render_template, flash
import os
import sys
import datetime
import urllib

import docutils.parsers.rst
import docutils.nodes
import re
from docutils.core import publish_parts

data_path = '../../exim-doc-devel'

ref_re = re.compile(r'(.+)\s+<ch(\d\d)_(\d\d)>', re.U)

def _ref_link_role(role, rawtext, text, lineno, inliner,
        options = {}, content = []):
    m = ref_re.match(text.encode('utf8'))
    txt = text
    ref = ''
    if m:
        ref = 'ch'+m.group(2)+'#ch'+m.group(2)+'-'+m.group(3)
        txt =  m.group(1).encode('utf8')
        
    node = docutils.nodes.reference(rawtext, txt, refuri = ref,
            **options)
    
    return [node], []

def content2html(content):

    settings = {
            'input_encoding': 'utf8',
            'output_encoding': 'utf8',
            }
    
    docutils.parsers.rst.roles.register_canonical_role('ref',
            _ref_link_role)

    parts = publish_parts(content,
            settings_overrides = settings,
            writer_name = "html")
    return parts

def diff2html(diff):
    from xml.sax.saxutils import escape
    s = '<div class="diff">'
    for l in diff.split('\n'):
        l = l.rstrip()
        if l.startswith("+") and not l.startswith("+++"):
            c = "add"
        elif l.startswith("-") and not l.startswith("---"):
            c = "remove"
        elif l.startswith(" "):
            c = "unchanged"
        elif l.startswith("@@"):
            c = "position"
        elif l.startswith("diff"):
            c = "header"
        else:
            c = "other"
            s += '<span class="%s">' % c + escape(l) + '</span>\n'
            # note there's no need to put <br/>s because the div.diff has
            # "white-space: pre" in the css
            s += '</div>'
    return s

def _add_times(commit):
    if 'author' in commit:
        author, epoch, tz = commit['author'].rsplit(' ', 2)
        epoch = float(epoch)
        commit['author'] = author
        commit['atime'] = datetime.datetime.fromtimestamp(epoch)

    if 'committer' in commit:
        committer, epoch, tz = commit['committer'].rsplit(' ', 2)
        epoch = float(epoch)
        commit['committer'] = committer
        commit['ctime'] = datetime.datetime.fromtimestamp(epoch)

class Article (object):
    def __init__(self, name, title = None, content = None,
            has_header = True):
        self.name = name
        self.qname = urllib.quote_plus(name, safe = "")
        self.updated = None

        self.loaded = False

        self.preloaded_title = title
        self.preloaded_content = content
        self.has_header = has_header

        # loaded on demand
        self.attrs = {}
        self._raw_content = ''

    def get_raw_content(self):
        if not self.loaded:
            self.load()
        return self._raw_content
    raw_content = property(fget = get_raw_content)

    def get_title(self):
        if not self.loaded:
            self.load()
        # use the name by default
        return self.attrs.get('title', self.name)
    title = property(fget = get_title)

    def load(self):
        try:
            if self.preloaded_content:
                raw = self.preloaded_content
                raw = [ s + '\n' for s in raw.split('\n') ]
                self.updated = datetime.datetime.now()
            else:
                fd = open(data_path + '/' + self.qname)
                raw = fd.readlines()
                stat = os.fstat(fd.fileno())
            self.updated = datetime.datetime.fromtimestamp(stat.st_mtime)
        except:
            raw = 'This page does *not* exist yet.'
            self.updated = datetime.datetime.now()

        hdr_lines = 0

        self._raw_content = ''.join(raw[hdr_lines:])
        self.loaded = True

        if self.preloaded_title:
            self.attrs['title'] = self.preloaded_title

    def save(self, newtitle, newcontent, raw = False):
        fd = open(data_path + '/' + self.qname, 'w+')
        if raw:
            fd.write(newcontent)
        else:
            fd.write('title: %s\n\n' % newtitle)
            fd.write(newcontent.rstrip() + '\n')
        fd.close()

        # invalidate our information
        self.loaded = False

    def remove(self):
        try:
            os.unlink(data_path + '/' + self.qname)
        except OSError, errno.ENOENT:
            pass

    def to_html(self):
        return content2html(self.raw_content)

class GitBackend:
    def __init__(self, repopath):
        self.repo = repopath
        self.prevdir = None
    
    def cdrepo(self):
        self.prevdir = os.getcwd()
        os.chdir(self.repo)

    def cdback(self):
        os.chdir(self.prevdir)

    def git(self, *args):
        # delay the import to avoid the hit on a regular page view
        import subprocess
        self.cdrepo()
        cmd = subprocess.Popen( ['git'] + list(args),
                stdin = subprocess.PIPE,
                stdout = subprocess.PIPE,
                stderr = sys.stderr )
        self.cdback()
        return cmd

    def gitq(self, *args):
        cmd = self.git(*args)
        stdout, stderr = cmd.communicate()
        return cmd.returncode

    def commit(self, msg, author = None):
        if not author:
            author = "Unknown <unknown@example.com>"
        # see if we have something to commit; if not, just return
        self.gitq('update-index', '--refresh')
        r = self.gitq('diff-index', '--exit-code', '--quiet', 'HEAD')
        if r == 0:
            return

        r = self.gitq('commit',
                '-m', msg,
                '--author', author)

        if r != 0:
            raise HistoryError, r

    def log(self, file = None, files = None):
        if not files:
            files = []
        if file:
            files.append(file)
        cmd = self.git("rev-list",
                "--all", "--pretty=raw",
                "HEAD", "--", *files)
        cmd.stdin.close()
        commit = { 'msg': '' }
        in_hdr = True
        l = cmd.stdout.readline()
        while l:
            if l != '\n' and not l.startswith(' '):
                name, value = l[:-1].split(' ', 1)
                if in_hdr:
                    commit[name] = value
                else:
                    # the previous commit has ended
                    _add_times(commit)
                    yield commit
                    commit = { 'msg': '' }
                    in_hdr = True
                    # continue reusing the line
                    continue
            else:
                if in_hdr:
                    in_hdr = False
                    
                if l.startswith('    '):
                    l = l[4:]
                commit['msg'] += l
            l = cmd.stdout.readline()
        # the last commit, if there is one
        if not in_hdr:
            _add_times(commit)
            yield commit
        cmd.wait()
        if cmd.returncode != 0:
            raise HistoryError, cmd.returncode
    
    def add(self, *files):
        r = self.gitq('add', "--", *files)
        if r != 0:
            raise HistoryError, r
    
    def remove(self, *files):
        r = self.gitq('rm', '-f', '--', *files)
        if r != 0:
            raise HistoryError, r
    
    def get_content(self, fname, commitid):
        cmd = self.git("show", "%s:%s" % (commitid, fname))
        content = cmd.stdout.read()
        cmd.wait()
        return content
    
    def get_commit(self, cid):
        cmd = self.git("rev-list", "-n1", "--pretty=raw", cid)
        out = cmd.stdout.readlines()
        cmd.wait()
        commit = { 'msg': '' }
        for l in out:
            if l != '\n' and not l.startswith(' '):
                name, value = l[:-1].split(' ', 1)
                commit[name] = value
            else:
                commit['msg'] += l
        _add_times(commit)
        return commit
    
    def get_diff(self, cid, artname):
        cmd = self.git("diff", cid + "^.." + cid, artname)
        out = cmd.stdout.read()
        cmd.wait()
        return out

class HistoryError (Exception):
    pass

class History:
    def __init__(self):
        self.be = GitBackend(data_path)

    def commit(self, msg, author = 'Gans Otto <wikiri@vyborg.ru>'):
        self.be.commit(msg, author = author)

    def log(self, fname):
        return self.be.log(file = fname)
    
    def add(self, *files):
        return self.be.add(*files)
    
    def remove(self, *files):
        return self.be.remove(*files)

    def get_content(self, fname, cid):
        return self.be.get_content(fname, cid)

    def get_commit(self, cid):
        return self.be.get_commit(cid)

    def get_diff(self, cid, artname):
        return self.be.get_diff(cid, artname)

app = Flask(__name__)
app.secret_key = 'CDxvoB5rtjP3fHfQHErq'
app.config['MAX_CONTENT_LENGTH'] = 1*1024*1024

@app.route('/')
def hello():
    return redirect(url_for('page', page="index"))
    return render_template('layout.html')

@app.route('/<page>')
def page(page):
    pagename = "%s.rst" % page
    art = Article(pagename)
    art_parts = art.to_html()
    art_body = art_parts['body_pre_docinfo'] + art_parts['body']
    art_info = 'updated on %s' % art.updated
    return render_template('page.html', artbody=art_body, artname=page,
            artinfo=art_info)

@app.route('/<page>/edit', methods=['GET', 'POST'])
def edit(page):
    pagename = "%s.rst" % page
    if request.method == 'GET':
        art = Article(pagename)
        art_raw = art.raw_content.decode('utf8')
        return render_template('edit.html', artname=page, artraw = art_raw)
    else:
        if 'preview' in request.form:
            art = Article(pagename, 
                    title = '', 
                    content = request.form['newcontent'], 
                    has_header = False)
            art_raw = art.raw_content
            art_parts = art.to_html()
            art_body = art_parts['body_pre_docinfo'] + art_parts['body']
            return render_template('edit.html', artname=page, artraw = art_raw,
                    preview = True, artbody=art_body)
        else:
            app.logger.warning('olo')
            comment = 'No comment'
            author = 'Gans Otto <wiki@vyborg.ru>'
            if request.form['comment']:
                comment = request.form['comment']
            h = History()
            a = Article(pagename)
            a.save(
                    '',
                    request.form['newcontent'].encode('utf8').replace('\r\n','\n'),
                    raw=True
                    )
            h.add(a.qname)
            h.commit(msg = comment, author = author)
            art_parts = a.to_html()
            art_body = art_parts['body_pre_docinfo'] + art_parts['body']
            return redirect(url_for('page',page=page))

@app.route('/<page>/log')
def log(page):
    pagename = "%s.rst" % page
    a = Article(pagename)
    commits = History().log(a.qname)
    return render_template('log.html', artname=page, commits=commits)

@app.route('/<page>/<rev>/view')
def revview(page,rev):
    pagename = "%s.rst" % page
    oldcontent = History().get_content(Article(pagename).qname, rev)
    art = Article(pagename, content = oldcontent)
    art_parts = art.to_html()
    art_body = art_parts['body_pre_docinfo'] + art_parts['body']
    art_info = 'updated on %s' % art.updated
    return render_template('page.html', artbody=art_body, artname=page,
            artinfo=art_info)

@app.route('/<page>/<rev>/diff')
def diffview(page,rev):
    pagename = "%s.rst" % page
    diff = History().get_diff(rev, pagename)
    diffhtml = diff2html(diff).decode('utf8')
    return render_template('page.html', artname=page, artbody=diffhtml)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'GET' and 'logged_in' in session:
        return redirect(url_for('hello'))
    if request.method == 'POST':
        if request.form['username'] != 'test':
            error = 'Invalid username'
        elif request.form['password'] != 'tost':
            error = 'Invalid password'
        else:
            session['logged_in'] = True
            flash('You were logged in')
            return redirect(url_for('hello'))
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('You were logged out')
    return redirect(url_for('hello'))

if __name__ == '__main__':
    app.run(debug=True)

