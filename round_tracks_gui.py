# -*- coding: utf-8 -*-

###########################################################################
## Python code generated with wxFormBuilder (version Oct 26 2018)
## http://www.wxformbuilder.org/
##
## PLEASE DO *NOT* EDIT THIS FILE!
###########################################################################

import wx
import wx.xrc
import wx.dataview

###########################################################################
## Class RoundTracksDialog
###########################################################################

class RoundTracksDialog ( wx.Dialog ):

	def __init__( self, parent ):
		wx.Dialog.__init__ ( self, parent, id = wx.ID_ANY, title = u"Track Rounding Parameters", pos = wx.DefaultPosition, size = wx.DefaultSize, style = wx.DEFAULT_DIALOG_STYLE|wx.RESIZE_BORDER )

		import sys
		if sys.version_info[0] == 2:
			self.SetSizeHintsSz( wx.DefaultSize, wx.DefaultSize )
		else:
			self.SetSizeHints( wx.DefaultSize, wx.DefaultSize )

		grid_vert = wx.FlexGridSizer( 4, 1, 0, 0 )
		grid_vert.AddGrowableCol( 0 )
		grid_vert.AddGrowableRow( 1 )
		grid_vert.SetFlexibleDirection( wx.BOTH )
		grid_vert.SetNonFlexibleGrowMode( wx.FLEX_GROWMODE_SPECIFIED )

		self.blurb = wx.StaticText( self, wx.ID_ANY, u"Rounds track corners for each enabled net class. Corners that can't be rounded are marked on the User.Comments layer. Run DRC after rounding.", wx.DefaultPosition, wx.DefaultSize, 0 )
		self.blurb.Wrap( self.FromDIP( 600 ) )
		grid_vert.Add( self.blurb, 1, wx.EXPAND|wx.ALL, 5 )

		self.netclasslist = wx.dataview.DataViewListCtrl( self, wx.ID_ANY, wx.DefaultPosition, wx.DefaultSize, 0 )
		self.netclasslist.SetMinSize( wx.Size( 450,100 ) )

		self.net_class = self.netclasslist.AppendTextColumn( u"Net Class", wx.dataview.DATAVIEW_CELL_INERT, self.FromDIP( 300 ), wx.ALIGN_LEFT, wx.dataview.DATAVIEW_COL_RESIZABLE|wx.dataview.DATAVIEW_COL_SORTABLE )
		self.do_rounding = self.netclasslist.AppendToggleColumn( u"Round", wx.dataview.DATAVIEW_CELL_ACTIVATABLE, self.FromDIP( 60 ), wx.ALIGN_CENTER, wx.dataview.DATAVIEW_COL_SORTABLE )
		self.scaling = self.netclasslist.AppendTextColumn( u"Target Radius (mm)", wx.dataview.DATAVIEW_CELL_EDITABLE, self.FromDIP( 150 ), wx.ALIGN_CENTER, wx.dataview.DATAVIEW_COL_SORTABLE )
		grid_vert.Add( self.netclasslist, 1, wx.EXPAND|wx.TOP|wx.RIGHT|wx.LEFT, 5 )

		grid_horiz = wx.FlexGridSizer( 0, 4, 0, 0 )
		grid_horiz.AddGrowableCol( 3 )
		grid_horiz.SetFlexibleDirection( wx.BOTH )
		grid_horiz.SetNonFlexibleGrowMode( wx.FLEX_GROWMODE_SPECIFIED )

		self.do_create = wx.CheckBox( self, wx.ID_ANY, u"Create a new file", wx.DefaultPosition, wx.DefaultSize, 0 )
		self.do_create.SetValue(False)
		grid_horiz.Add( self.do_create, 0, wx.ALL|wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT, 5 )

		self.avoid_junctions = wx.CheckBox( self, wx.ID_ANY, u"Ignore junctions", wx.DefaultPosition, wx.DefaultSize, 0 )
		self.avoid_junctions.SetValue(False)
		grid_horiz.Add( self.avoid_junctions, 0, wx.ALL|wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT, 5 )

		self.min_radius_label = wx.StaticText( self, wx.ID_ANY, u"Min radius (mm)", wx.DefaultPosition, wx.DefaultSize, 0 )
		self.min_radius_label.Wrap( -1 )
		grid_horiz.Add( self.min_radius_label, 0, wx.ALL|wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT, 5 )

		self.min_radius = wx.TextCtrl( self, wx.ID_ANY, u"0.1", wx.DefaultPosition, wx.Size( 60,-1 ), 0 )
		grid_horiz.Add( self.min_radius, 0, wx.ALL|wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT, 5 )

		self.refill_zones = wx.CheckBox( self, wx.ID_ANY, u"Refill zones", wx.DefaultPosition, wx.DefaultSize, 0 )
		self.refill_zones.SetValue(False)
		grid_horiz.Add( self.refill_zones, 0, wx.ALL|wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT, 5 )

		self.selected_only = wx.CheckBox( self, wx.ID_ANY, u"Selected tracks only (none selected)", wx.DefaultPosition, wx.DefaultSize, 0 )
		self.selected_only.SetValue(False)
		self.selected_only.Enable(False)
		grid_horiz.Add( self.selected_only, 0, wx.ALL|wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT, 5 )

		grid_vert.Add( grid_horiz, 1, wx.EXPAND, 5 )

		grid_buttons = wx.BoxSizer( wx.HORIZONTAL )

		self.unround = wx.Button( self, wx.ID_ANY, u"Unround", wx.DefaultPosition, wx.DefaultSize, 0 )
		grid_buttons.Add( self.unround, 0, wx.ALL|wx.ALIGN_CENTER_VERTICAL, 5 )

		grid_buttons.AddStretchSpacer( 1 )

		std_buttons = wx.StdDialogButtonSizer()
		self.apply = wx.Button( self, wx.ID_OK, u"Run" )
		self.apply.SetDefault()
		std_buttons.AddButton( self.apply )
		self.close_button = wx.Button( self, wx.ID_CANCEL, u"Close" )
		std_buttons.AddButton( self.close_button )
		std_buttons.Realize()
		grid_buttons.Add( std_buttons, 0, wx.ALL|wx.ALIGN_CENTER_VERTICAL, 5 )
		grid_vert.Add( grid_buttons, 1, wx.EXPAND, 5 )

		self.SetSizer( grid_vert )
		self.Layout()
		grid_vert.Fit( self )
		best = self.GetSize()
		initial = self.FromDIP( wx.Size( 640, 420 ) )
		self.SetSize( wx.Size( max( best.x, initial.x ), max( best.y, initial.y ) ) )

		self.Centre( wx.BOTH )

		# Connect Events
		self.Bind( wx.EVT_CLOSE, self.on_close )
		self.netclasslist.Bind( wx.dataview.EVT_DATAVIEW_ITEM_EDITING_DONE, self.on_item_editing, id = wx.ID_ANY )
		self.apply.Bind( wx.EVT_BUTTON, self.run )
		self.unround.Bind( wx.EVT_BUTTON, self.unrun )
		self.close_button.Bind( wx.EVT_BUTTON, self.on_close )

	def __del__( self ):
		pass


	# Virtual event handlers, override them in your derived class
	def on_close( self, event ):
		event.Skip()

	def on_item_editing( self, event ):
		event.Skip()

	def run( self, event ):
		event.Skip()


