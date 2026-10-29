// Entry for web/static/vendor/tiptap/tiptap.min.js (built by build.mjs).
//
// Tiptap 2 + ProseMirror + y-prosemirror + markdown-it, as one IIFE that
// sets window.DomovoiTiptap. web/static/doc_editor.jsx builds the
// Documents editor from it (window.DomovoiDocEditor).
//
// `yjs` is NOT inside this bundle: build.mjs points every import of it at
// window.Y, which web/static/vendor/yjs/yjs.min.js sets. Load that first.
// y-prosemirror lives here rather than beside Yjs because it must share
// this bundle's ProseMirror (its plugin keys and node classes are checked
// by identity).
import { Editor, Extension, Node, Mark, mergeAttributes, getHTMLFromFragment } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import Underline from '@tiptap/extension-underline';
import TextStyle from '@tiptap/extension-text-style';
import Color from '@tiptap/extension-color';
import Highlight from '@tiptap/extension-highlight';
import TextAlign from '@tiptap/extension-text-align';
import FontFamily from '@tiptap/extension-font-family';
import Link from '@tiptap/extension-link';
import Table from '@tiptap/extension-table';
import TableRow from '@tiptap/extension-table-row';
import TableHeader from '@tiptap/extension-table-header';
import TableCell from '@tiptap/extension-table-cell';
import TaskList from '@tiptap/extension-task-list';
import TaskItem from '@tiptap/extension-task-item';
import Image from '@tiptap/extension-image';
import Placeholder from '@tiptap/extension-placeholder';
import Collaboration from '@tiptap/extension-collaboration';
import CollaborationCursor from '@tiptap/extension-collaboration-cursor';
import * as pmState from '@tiptap/pm/state';
import * as pmView from '@tiptap/pm/view';
import * as pmModel from '@tiptap/pm/model';
import * as pmTables from '@tiptap/pm/tables';
import { MarkdownSerializer, defaultMarkdownSerializer } from 'prosemirror-markdown';
import MarkdownIt from 'markdown-it';
import * as yProsemirror from 'y-prosemirror';

/* global __VERSIONS__ */
window.DomovoiTiptap = {
  Editor, Extension, Node, Mark, mergeAttributes, getHTMLFromFragment,
  StarterKit, Underline, TextStyle, Color, Highlight, TextAlign, FontFamily,
  Link, Table, TableRow, TableHeader, TableCell, TaskList, TaskItem, Image,
  Placeholder, Collaboration, CollaborationCursor,
  pm: { state: pmState, view: pmView, model: pmModel, tables: pmTables },
  MarkdownSerializer, defaultMarkdownSerializer, MarkdownIt,
  yProsemirror,
  versions: __VERSIONS__,
};
