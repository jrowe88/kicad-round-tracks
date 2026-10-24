# Original example Copyright [2017] [Miles McCoo]
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#    http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# extensively modified by Julian Loiacono, Oct 2018
# updated and repacked as action plugin by Antoine Pintout, May 2019
# updated subdivision algorithm + native arc support by mitxela, 2021-2024

import pcbnew
import math
import os
import wx
import time
import traceback
try:
    from .round_tracks_utils import *
    from .round_tracks_gui import RoundTracksDialog
    from .round_tracks_board import ClassSetting, Settings, round_board, summary_text
    from .round_tracks_config import (parse_config, parse_length, read_new_file, format_config,
                                      MIN_RADIUS_DEFAULT)
except ImportError:
    from round_tracks_utils import *
    from round_tracks_gui import RoundTracksDialog
    from round_tracks_board import ClassSetting, Settings, round_board, summary_text
    from round_tracks_config import (parse_config, parse_length, read_new_file, format_config,
                                     MIN_RADIUS_DEFAULT)

RADIUS_DEFAULT = 2.0

class RoundTracks(RoundTracksDialog):

    def __init__(self, board, action):
        super(RoundTracks, self).__init__(None)
        self.board = board
        self.basefilename = os.path.splitext(board.GetFileName())[0]
        if self.basefilename.endswith('-rounded'):
            self.basefilename = self.basefilename[:-len('-rounded')]
        self.configfilepath = self.basefilename+".round-tracks-config"
        self.action = action
        self.netClassCount = 1
        self.prog = None
        text = self.read_config_text()
        self.settings = parse_config(text)
        self.do_create.SetValue(read_new_file(text))
        self.avoid_junctions.SetValue(self.settings.avoid_junctions)
        self.min_radius.SetValue(str(self.settings.min_radius_mm))
        self.refill_zones.SetValue(self.settings.refill_zones)
        self.selected_count = self.count_selected_tracks()
        if self.selected_count:
            self.selected_only.SetLabel("Selected tracks only (%d selected)" % self.selected_count)
            self.selected_only.SetValue(True)
            self.selected_only.Enable(True)

        c = self.settings.classes
        default = c.get("Default", ClassSetting(True, RADIUS_DEFAULT))
        self.netclasslist.AppendItem( ["Default", default.do_round, str(default.radius_mm)])
        for class_id in self.board.GetNetClasses():
            classname = str(class_id)
            self.netClassCount += 1
            cs = c.get(classname, ClassSetting(True, RADIUS_DEFAULT))
            self.netclasslist.AppendItem( [classname, cs.do_round, str(cs.radius_mm)])
        self.validate_all_data()


    def count_selected_tracks(self):
        """Selected tracks and arcs (not vias), counted when the dialog opens."""
        return sum(1 for t in self.board.GetTracks()
                   if t.IsSelected() and t.GetClass() in ('PCB_TRACK', 'PCB_ARC'))

    def run( self, event ):
        start = time.time()
        label = self.apply.GetLabel()
        self.apply.SetLabel("Working...")
        self.prog = None
        try:
            self.validate_all_data()
            self.save_config()

            self.prog = wx.ProgressDialog("Processing", "Rounding tracks...", 100, self, wx.PD_AUTO_HIDE|wx.PD_APP_MODAL|wx.PD_ELAPSED_TIME)
            self.prog.Pulse("Rounding tracks...")

            if self.do_create.IsChecked():
                new_name = self.basefilename+"-rounded.kicad_pcb"
                self.board.SetFileName(new_name)

            stats = round_board(self.board, self.settings, progress=self.pulse)

            if self.settings.refill_zones:
                # opt-in: if m_AutoRefillZones is set KiCad may refill anyway, but
                # PCBNEW_SETTINGS is not exposed to swig so we can't tell
                self.prog.Pulse("Refilling zones...")
                wx.Yield()
                filler = pcbnew.ZONE_FILLER(self.board)
                filler.Fill(self.board.Zones())
        except Exception:
            self.close_progress()
            self.report_error("Rounding failed.")
            return
        finally:
            self.close_progress()
            self.apply.SetLabel(label)

        message = "Done, took {:.3f} seconds\n\n{}".format(time.time()-start, summary_text(stats))
        if self.settings.only_selection:
            message += "\n\nSelection only: %d tracks." % self.selected_count
        wx.MessageBox(message, parent=self)
        self.EndModal(wx.ID_OK)

    def pulse(self, message):
        """Progress callback for round_board: one pulse per net."""
        if self.prog:
            self.prog.Pulse(message)
            wx.Yield()

    def close_progress(self):
        if self.prog:
            self.prog.Destroy()
            self.prog = None
            wx.Yield()

    def report_error(self, what):
        wx.MessageBox(what + " The board may be partly changed; Undo (Ctrl+Z) puts it back.\n\n"
                      + traceback.format_exc(), "Round tracks", wx.OK | wx.ICON_ERROR, parent=self)


    def unrun( self, event ):
        try:
            removed, hairpins = self.unround_arcs()
        except Exception:
            self.report_error("Unround failed.")
            return

        if removed or hairpins:
            lines = []
            if hairpins:
                lines.append("%d hairpin arc%s (turning nearly 180 degrees) left in place: the corner %s came from is too far away to recover."
                             % (hairpins, "" if hairpins == 1 else "s", "it" if hairpins == 1 else "they"))
            if removed:
                lines.append("%d arc%s with a nonsensical angle (usually a very, very small arc) removed."
                             % (removed, "" if removed == 1 else "s"))
            wx.MessageBox("Some arcs failed to unround.\n\n" + "\n\n".join(lines), style=wx.ICON_ERROR)

        wx.MessageBox("Unrounded!\n\nPlease note, due to rounding errors, a round trip may not exactly match the original.", parent=self)

        self.EndModal(wx.ID_OK)


    def unround_arcs(self):
        """Turn arcs back into corners; return (arcs removed, hairpin arcs left alone)."""
        anySelected = self.selected_only.IsChecked()

        board = self.board
        netcodes = board.GetNetsByNetcode()
        allTracks = board.GetTracks()

        tracksStartsToExtend = []
        tracksEndsToExtend = []
        tracksToAdd = []
        tracksToRemove = []
        removed = 0
        hairpins = 0

        def findExtendTrack( pos, angle, intersection, tracksInNet, arc ):
            angle = normalizeAngle(angle)
            layer = arc.GetLayer()
            for t in tracksInNet:
                if t.GetLayer() != layer:
                    continue
                if similarPoints(t.GetStart(), pos):
                    if similarAngle(angle, getTrackAngle(t)):
                        #t.SetStart(intersection)
                        tracksStartsToExtend.append( (t, intersection) )
                        return True
                if similarPoints(t.GetEnd(), pos):
                    if similarAngle(angle, getTrackAngle(t)+math.pi):
                        #t.SetEnd(intersection)
                        tracksEndsToExtend.append( (t, intersection) )
                        return True
            # before creating new track, look for new co-linear tracks
            width = arc.GetWidth()
            net = arc.GetNetCode()
            for i,t in enumerate(tracksToAdd):
                (sp,ep,w,l,n) = t
                if (n==net and l==layer and w==width and similarPoints(sp, pos) and
                    similarAngle(math.atan2(ep.y-sp.y,ep.x-sp.x), math.atan2(pos.y-intersection.y,pos.x-intersection.x))):
                    tracksToAdd[i]= (intersection, ep ,w,l,n)
                    return False

            tracksToAdd.append((pos, intersection, width, layer, net))
            return False

        for netcode, net in netcodes.items():
            tracksInNet = []
            arcsInNet = []
            intersectionStack = []

            for t in allTracks:
                if t.GetNetCode() == netcode:
                    if t.GetClass() == 'PCB_ARC' and (not anySelected or t.IsSelected()):
                        arcsInNet.append(t)
                    # we may need to extend tracks outside of selection
                    elif t.GetClass() == 'PCB_TRACK':
                        tracksInNet.append(t)

            # check arc angle < pi ? oversize arcs shouldn't exist

            for a in arcsInNet:
                start  = a.GetStart()
                end    = a.GetEnd()
                center = a.GetCenter()
                startAngle = a.GetArcAngleStart().AsRadians()
                endAngle = a.GetArcAngleEnd().AsRadians()
                halfAngle = a.GetAngle().AsRadians()/2
                interdistance = a.GetRadius() / math.cos(halfAngle)
                if interdistance > 2**32:
                    removed += 1
                    tracksToRemove.append(a)
                    continue
                if interdistance > 10 * a.GetRadius():
                    # hairpin arc (turning nearly 180 degrees): the corner it came
                    # from is too far away to recover, so it stays as it is
                    hairpins += 1
                    continue
                if a.IsCCW():
                    interAngle = startAngle +halfAngle
                else:
                    interAngle = endAngle -halfAngle

                intersection = pcbnew.VECTOR2I(
                    center.x + int(interdistance*math.cos(interAngle)),
                    center.y + int(interdistance*math.sin(interAngle))
                    )
                adjust = math.pi/2 if a.IsCCW() else -math.pi/2

                intersectionStack.append( (intersection, a.GetLayer()) )

                findExtendTrack( start, startAngle-adjust, intersection, tracksInNet, a )
                findExtendTrack( end, endAngle+adjust, intersection, tracksInNet, a )
                tracksToRemove.append(a)

            # find junctions
            for i,iL in intersectionStack:
                junction = []
                for j,jL in intersectionStack:
                    if i==j or iL!=jL: continue
                    if ( ((i.x > j.x - unpickJunctionTolerance)
                     and  (i.x < j.x + unpickJunctionTolerance))
                     and ((i.y > j.y - unpickJunctionTolerance)
                     and  (i.y < j.y + unpickJunctionTolerance))):
                        junction.append(j)
                if len(junction)>0:
                    junction.append(i)
                    x = 0
                    y = 0
                    for j in junction:
                        x += j.x
                        y += j.y
                    newCenter = pcbnew.VECTOR2I(int(x/len(junction)), int(y/len(junction)))
                    extremities = []
                    # correct positions of new track ends
                    for idx,(t,s) in enumerate(tracksStartsToExtend):
                        if s in junction:
                            tracksStartsToExtend[idx] = t,newCenter
                            extremities.append(t.GetStart())
                    for idx,(t,s) in enumerate(tracksEndsToExtend):
                        if s in junction:
                            tracksEndsToExtend[idx] = t,newCenter
                            extremities.append(t.GetEnd())
                    for idx, (sp, ep, width, layer, n) in enumerate(tracksToAdd):
                        if layer==iL and net==n and sp in junction:
                            tracksToAdd[idx] = newCenter, ep, width, layer, n
                            extremities.append(ep)

                    # remove unmodified, straight tracks that pass through the junction
                    for t in tracksInNet:
                        if t.GetLayer()==iL and t.GetStart() in extremities and t.GetEnd() in extremities:
                            tracksToRemove.append(t)

            # junctions may stack the same track start/end twice
            for t, s in tracksStartsToExtend:
                t.SetStart(s)
            tracksStartsToExtend = []

            for t, s in tracksEndsToExtend:
                t.SetEnd(s)
            tracksEndsToExtend = []

        for sp, ep, width, layer, net in tracksToAdd:
            track = pcbnew.PCB_TRACK(board)
            track.SetStart(sp)
            track.SetEnd(ep)
            track.SetWidth(width)
            track.SetLayer(layer)
            board.Add(track)
            track.SetNetCode(net)
            if anySelected:
                track.SetSelected()

        for t in tracksToRemove:
            board.Remove(t)

        return removed, hairpins


    def on_close( self, event ):
        self.EndModal(wx.ID_OK)

    def on_item_editing( self, event ):
        if bool(self.netclasslist):
            self.validate_all_data()

    def read_config_text(self):
        try:
            if os.path.isfile(self.configfilepath):
                with open(self.configfilepath, "r") as configfile:
                    return configfile.read()
        except (OSError, UnicodeDecodeError):
            pass
        return ""

    def save_config(self):
        try:
            with open(self.configfilepath, "w") as configfile:
                configfile.write(format_config(self.settings, self.do_create.IsChecked()))
        except PermissionError:
            pass

    def validate_all_data (self):
        classes = {}
        for i in range(self.netClassCount):
            radius = parse_length(self.netclasslist.GetTextValue(i, 2), None)
            if radius is None:
                radius = RADIUS_DEFAULT
                self.netclasslist.SetTextValue(str(RADIUS_DEFAULT), i, 2)
            classes[self.netclasslist.GetTextValue(i, 0)] = ClassSetting(
                self.netclasslist.GetToggleValue(i, 1), radius)
        min_radius = parse_length(self.min_radius.GetValue(), None)
        if min_radius is None:
            min_radius = MIN_RADIUS_DEFAULT
            self.min_radius.SetValue(str(MIN_RADIUS_DEFAULT))
        self.settings = Settings(classes=classes, min_radius_mm=min_radius,
                                 avoid_junctions=self.avoid_junctions.IsChecked(),
                                 only_selection=self.selected_only.IsChecked(),
                                 refill_zones=self.refill_zones.IsChecked())


class ActionRoundTracks( pcbnew.ActionPlugin ):
 
    def defaults( self ):
        self.name = "Round tracks"
        self.category = "Modify PCB"
        self.description = "Native arc-based track rounding"
        self.show_toolbar_button = True
        self.icon_file_name = os.path.join(os.path.dirname(__file__), "./round_tracks.png")
        self.dark_icon_file_name = os.path.join(os.path.dirname(__file__), "./round_tracks_dark.png")

    def Run( self ):
        if pcbnew.GetBuildVersion()=='(7.0.0)':
            wx.MessageBox("Sorry, this plugin is not compatible with KiCad 7.0.0\n\nIt should work with 7.0.1, if that's not out yet you can try KiCad nightly.")
            return
        board = pcbnew.GetBoard()
        rt = RoundTracks(board, self)
        rt.ShowModal()
        rt.Destroy()

        # this shouldn't be needed, but without it, arcs are sometimes not acknowledged as tangential until they've been moved or the file re-opened
        # as of kicad 6.0.0, doing pcbnew.Refresh() here causes the arcs to glitch out and create duplicate shapes that can't be deleted
        pcbnew.UpdateUserInterface()
