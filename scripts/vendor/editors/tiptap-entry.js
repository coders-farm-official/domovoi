// Entry for web/static/vendor/tiptap/tiptap.min.js (built by build.mjs).
//
// Tiptap 3 + ProseMirror + y-tiptap + markdown-it, as one IIFE that
// sets window.DomovoiTiptap. web/static/doc_editor.jsx builds the
// Documents editor from it (window.DomovoiDocEditor).
//
// `yjs` is NOT inside this bundle: build.mjs points every import of it at
// window.Y, which web/static/vendor/yjs/yjs.min.js sets. Load that first.
// y-tiptap (Tiptap 3's fork of y-prosemirror: the same Yjs layout) lives
// here rather than beside Yjs because it must share this bundle's
// ProseMirror (its plugin keys and node classes are checked by identity).
// Tiptap 3 moved several extensions into shared packages (text-style,
// table, list, extensions); window.DomovoiTiptap keeps the old names,
// except CollaborationCursor (now CollaborationCaret) and yTiptap.
import { Editor, Extension, Node, Mark, mergeAttributes, getHTMLFromFragment } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import Underline from '@tiptap/extension-underline';
import { TextStyle, Color, FontFamily } from '@tiptap/extension-text-style';
import Highlight from '@tiptap/extension-highlight';
import TextAlign from '@tiptap/extension-text-align';
import Link from '@tiptap/extension-link';
import { Table, TableRow, TableHeader, TableCell } from '@tiptap/extension-table';
import { TaskList, TaskItem } from '@tiptap/extension-list';
import Image from '@tiptap/extension-image';
import { Placeholder } from '@tiptap/extensions';
import Collaboration from '@tiptap/extension-collaboration';
import CollaborationCaret from '@tiptap/extension-collaboration-caret';
import * as pmState from '@tiptap/pm/state';
import * as pmView from '@tiptap/pm/view';
import * as pmModel from '@tiptap/pm/model';
import * as pmTables from '@tiptap/pm/tables';
import { MarkdownSerializer, defaultMarkdownSerializer } from 'prosemirror-markdown';
import MarkdownIt from 'markdown-it';
import * as yTiptap from '@tiptap/y-tiptap';

/* global __VERSIONS__ */
window.DomovoiTiptap = {
  Editor, Extension, Node, Mark, mergeAttributes, getHTMLFromFragment,
  StarterKit, Underline, TextStyle, Color, Highlight, TextAlign, FontFamily,
  Link, Table, TableRow, TableHeader, TableCell, TaskList, TaskItem, Image,
  Placeholder, Collaboration, CollaborationCaret,
  pm: { state: pmState, view: pmView, model: pmModel, tables: pmTables },
  MarkdownSerializer, defaultMarkdownSerializer, MarkdownIt,
  yTiptap,
  versions: __VERSIONS__,
};
