"""Jupyter viewers for AiiDA data objects."""
# pylint: disable=no-self-use

import base64
import warnings
import numpy as np
import ipywidgets as ipw
from IPython.display import display
import nglview
from ase import Atoms

from traitlets import Instance, Int, List, Union, default, link, observe, validate
from aiida.orm import Node

from .utils import string_range_to_list, list_to_string_range
from .misc import CopyToClipboardButton


def viewer(obj, downloadable=True, **kwargs):
    """Display AiiDA data types in Jupyter notebooks.

    :param downloadable: If True, add link/button to download the content of displayed AiiDA object.
    :type downloadable: bool

    Returns the object itself if the viewer wasn't found."""
    if not isinstance(obj, Node):  # only working with AiiDA nodes
        warnings.warn("This viewer works only with AiiDA objects, got {}".format(type(obj)))
        return obj

    try:
        _viewer = AIIDA_VIEWER_MAPPING[obj.node_type]
        return _viewer(obj, downloadable=downloadable, **kwargs)
    except (KeyError) as exc:
        if obj.node_type in str(exc):
            warnings.warn("Did not find an appropriate viewer for the {} object. Returning the object "
                          "itself.".format(type(obj)))
            return obj
        raise exc


class DictViewer(ipw.HTML):
    """Viewer class for Dict object.

    :param parameter: Dict object to be viewed
    :type parameter: Dict
    :param downloadable: If True, add link/button to download the content of the object
    :type downloadable: bool"""

    def __init__(self, parameter, downloadable=True, **kwargs):
        super().__init__(**kwargs)
        import pandas as pd
        # Here we are defining properties of 'df' class (specified while exporting pandas table into html).
        # Since the exported object is nothing more than HTML table, all 'standard' HTML table settings
        # can be applied to it as well.
        # For more information on how to controle the table appearance please visit:
        # https://css-tricks.com/complete-guide-table-element/
        self.value = '''
        <style>
            .df { border: none; }
            .df tbody tr:nth-child(odd) { background-color: #e5e7e9; }
            .df tbody tr:nth-child(odd):hover { background-color:   #f5b7b1; }
            .df tbody tr:nth-child(even):hover { background-color:  #f5b7b1; }
            .df tbody td { min-width: 300px; text-align: center; border: none }
            .df th { text-align: center; border: none;  border-bottom: 1px solid black;}
        </style>
        '''
        pd.set_option('max_colwidth', 40)
        dataf = pd.DataFrame([(key, value) for key, value in sorted(parameter.get_dict().items())],
                             columns=['Key', 'Value'])
        self.value += dataf.to_html(classes='df', index=False)  # specify that exported table belongs to 'df' class
        # this is used to setup table's appearance using CSS
        if downloadable:
            payload = base64.b64encode(dataf.to_csv(index=False).encode()).decode()
            fname = '{}.csv'.format(parameter.pk)
            to_add = """Download table in csv format: <a download="{filename}"
            href="data:text/csv;base64,{payload}" target="_blank">{title}</a>"""
            self.value += to_add.format(filename=fname, payload=payload, title=fname)


class _StructureDataBaseViewer(ipw.VBox):
    """Base viewer class for AiiDA structure or trajectory objects.

    :param configure_view: If True, add configuration tabs
    :type configure_view: bool"""

    selection = List(Int)
    supercell = List(Int)
    DEFAULT_SELECTION_OPACITY = 0.2
    DEFAULT_SELECTION_RADIUS = 6
    DEFAULT_SELECTION_COLOR = 'green'

    def __init__(self, configure_view=True, **kwargs):
        # Defining viewer box.

        # 1. Nglviwer
        self._viewer = nglview.NGLWidget()
        self._viewer.camera = 'orthographic'
        self._viewer.observe(self._on_atom_click, names='picked')
        self._viewer.stage.set_parameters(mouse_preset='pymol')

        # 2. Camera type.
        camera_type = ipw.ToggleButtons(options={
            'Orthographic': 'orthographic',
            'Perspective': 'perspective'
        },
                                        description='Camera type:',
                                        value='orthographic',
                                        layout={"align_self": "flex-end"},
                                        style={'button_width': '115.5px'},
                                        orientation='vertical')

        def change_camera(change):
            self._viewer.camera = change['new']

        camera_type.observe(change_camera, names="value")
        view_box = ipw.VBox([self._viewer, camera_type])

        # Constructing configuration box
        if configure_view:
            configuration_box = ipw.Tab(layout=ipw.Layout(flex='1 1 auto', width='auto'))
            configuration_box.children = [self._selection_tab(), self._appearance_tab(), self._download_tab()]
            for i, title in enumerate(["Selection", "Appearance", "Download"]):
                configuration_box.set_title(i, title)
            children = [ipw.HBox([view_box, configuration_box])]
            view_box.layout = {'width': "60%"}
        else:
            children = [view_box]

        if 'children' in kwargs:
            children += kwargs.pop('children')

        super().__init__(children, **kwargs)

    def _selection_tab(self):
        """Defining the selection tab."""

        # 1. Selected atoms.
        self._selected_atoms = ipw.Text(description='Selected atoms:', value='', style={'description_width': 'initial'})

        # 2. Copy to clipboard
        copy_to_clipboard = CopyToClipboardButton(description="Copy to clipboard")
        link((self._selected_atoms, 'value'), (copy_to_clipboard, 'value'))

        # 3. Informing about wrong syntax.
        self.wrong_syntax = ipw.HTML(
            value="""<i class="fa fa-times" style="color:red;font-size:2em;" ></i> wrong syntax""",
            layout={'visibility': 'hidden'})

        # 4. Button to clear selection.
        clear_selection = ipw.Button(description="Clear selection")
        clear_selection.on_click(lambda _: self.set_trait('selection', list()))  # lambda cannot contain assignments

        # 5. Button to apply selection
        apply_selection = ipw.Button(description="Apply selection")
        apply_selection.on_click(self.apply_selection)

        self.selection_info = ipw.HTML()

        return ipw.VBox([
            ipw.HBox([self._selected_atoms, self.wrong_syntax]),
            ipw.HBox([copy_to_clipboard, clear_selection, apply_selection]),
            self.selection_info,
        ])

    def _appearance_tab(self):
        """Defining the appearance tab."""

        # 1. Supercell
        def change_supercell(_=None):
            self.supercell = [_supercell[0].value, _supercell[1].value, _supercell[2].value]

        _supercell = [
            ipw.BoundedIntText(value=1, min=1, layout={"width": "30px"}),
            ipw.BoundedIntText(value=1, min=1, layout={"width": "30px"}),
            ipw.BoundedIntText(value=1, min=1, layout={"width": "30px"}),
        ]
        for elem in _supercell:
            elem.observe(change_supercell, names='value')
        supercell_selector = ipw.HBox([ipw.HTML(description="Super cell:")] + _supercell)

        # 2. Choose background color.
        background_color = ipw.ColorPicker(description="Background")
        link((background_color, 'value'), (self._viewer, 'background'))
        background_color.value = 'white'

        # 3. Center button.
        center_button = ipw.Button(description="Center")
        center_button.on_click(lambda c: self._viewer.center())

        return ipw.VBox([supercell_selector, background_color, center_button])

    def _download_tab(self):
        """Defining the download tab."""

        # 1. Choose download file format.
        self.file_format = ipw.Dropdown(options=['xyz', 'cif'], layout={"width": "200px"}, description="File format:")

        # 2. Download button.
        self.download_btn = ipw.Button(description="Download")
        self.download_btn.on_click(self.download)
        self.download_box = ipw.VBox(
            children=[ipw.Label("Download as file:"),
                      ipw.HBox([self.file_format, self.download_btn])])

        # 3. Screenshot button
        self.screenshot_btn = ipw.Button(description="Screenshot", icon='camera')
        self.screenshot_btn.on_click(lambda _: self._viewer.download_image())
        self.screenshot_box = ipw.VBox(children=[ipw.Label("Create a screenshot:"), self.screenshot_btn])

        return ipw.VBox([self.download_box, self.screenshot_box])

    def _on_atom_click(self, _=None):
        """Update selection when clicked on atom."""
        if 'atom1' not in self._viewer.picked.keys():
            return  # did not click on atom
        index = self._viewer.picked['atom1']['index']
        selection = self.selection.copy()

        if selection:
            if index not in selection:
                selection.append(index)
            else:
                selection.remove(index)
        else:
            selection = [index]

        self.selection = selection

    def highlight_atoms(self,
                        vis_list,
                        color=DEFAULT_SELECTION_COLOR,
                        size=DEFAULT_SELECTION_RADIUS,
                        opacity=DEFAULT_SELECTION_OPACITY):
        """Highlighting atoms according to the provided list."""
        if not hasattr(self._viewer, "component_0"):
            return
        self._viewer._remove_representations_by_name(repr_name='selected_atoms')  # pylint:disable=protected-access
        self._viewer.add_ball_and_stick(  # pylint:disable=no-member
            name="selected_atoms",
            selection=list() if vis_list is None else vis_list,
            color=color,
            aspectRatio=size,
            opacity=opacity)

    @default('supercell')
    def _default_supercell(self):
        return [1, 1, 1]

    @default('selection')
    def _default_selection(self):
        return list()

    @validate('selection')
    def _validate_selection(self, provided):
        return list(provided['value'])

    @observe('selection')
    def _observe_selection(self, _=None):
        self.highlight_atoms(self.selection)
        self._selected_atoms.value = list_to_string_range(self.selection, shift=1)

    def apply_selection(self, _=None):
        """Apply selection specified in the text field."""
        selection_string = self._selected_atoms.value
        expanded_selection, syntax_ok = string_range_to_list(self._selected_atoms.value, shift=-1)
        self.wrong_syntax.layout.visibility = 'hidden' if syntax_ok else 'visible'
        self.selection = expanded_selection
        self._selected_atoms.value = selection_string  # Keep the old string for further editing.

    def download(self, change=None):  # pylint: disable=unused-argument
        """Prepare a structure for downloading."""
        self._download(payload=self._prepare_payload(), filename='structure.' + self.file_format.value)

    @staticmethod
    def _download(payload, filename):
        """Download payload as a file named as filename."""
        from IPython.display import Javascript
        javas = Javascript("""
            var link = document.createElement('a');
            link.href = "data:;base64,{payload}"
            link.download = "{filename}"
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            """.format(payload=payload, filename=filename))
        display(javas)

    def _prepare_payload(self, file_format=None):
        """Prepare binary information."""
        from tempfile import NamedTemporaryFile
        file_format = file_format if file_format else self.file_format.value
        tmp = NamedTemporaryFile()
        self.structure.write(tmp.name, format=file_format)  # pylint: disable=no-member
        with open(tmp.name, 'rb') as raw:
            return base64.b64encode(raw.read()).decode()

    @property
    def thumbnail(self):
        return self._prepare_payload(file_format='png')


class StructureDataViewer(_StructureDataBaseViewer):
    """Viewer class for AiiDA structure objects.

    Attributes:
        structure (Atoms, StructureData, CifData): Trait that contains a structure object,
        which was initially provided to the viewer. It can be either directly set to an
        ASE Atoms object or to AiiDA structure object containing `get_ase()` method.

        displayed_structure (Atoms): Trait that contains a structure object that is
        currently displayed (super cell, for example). The trait is generated automatically
        and can't be set outside of the class.
    """
    structure = Union([Instance(Atoms), Instance(Node)], allow_none=True)
    displayed_structure = Instance(Atoms, allow_none=True, read_only=True)

    def __init__(self, structure=None, **kwargs):
        super().__init__(**kwargs)
        self.structure = structure
        #self.supercell.observe(self.repeat, names='value')

    @observe('supercell')
    def repeat(self, _=None):
        if self.structure is not None:
            self.set_trait('displayed_structure', self.structure.repeat(self.supercell))

    @validate('structure')
    def _valid_structure(self, change):  # pylint: disable=no-self-use
        """Update structure."""
        structure = change['value']

        if structure is None:
            return None  # if no structure provided, the rest of the code can be skipped

        if isinstance(structure, Atoms):
            return structure
        if isinstance(structure, Node):
            return structure.get_ase()
        raise ValueError("Unsupported type {}, structure must be one of the following types: "
                         "ASE Atoms object, AiiDA CifData or StructureData.")

    @observe('structure')
    def _update_displayed_structure(self, change):
        """Update displayed_structure trait after the structure trait has been modified."""
        # Remove the current structure(s) from the viewer.
        if change['new'] is not None:
            self.set_trait('displayed_structure', change['new'].repeat(self.supercell))
        else:
            self.set_trait('displayed_structure', None)

    @observe('displayed_structure')
    def _update_structure_viewer(self, change):
        """Update the view if displayed_structure trait was modified."""
        with self.hold_trait_notifications():
            for comp_id in self._viewer._ngl_component_ids:  # pylint: disable=protected-access
                self._viewer.remove_component(comp_id)
            self.selection = list()
            if change['new'] is not None:
                self._viewer.add_component(nglview.ASEStructure(change['new']))
                self._viewer.clear()
                self._viewer.add_ball_and_stick(aspectRatio=4)  # pylint: disable=no-member
                self._viewer.add_unitcell()  # pylint: disable=no-member

    def create_selection_info(self):
        """Create information to be displayed with selected atoms"""

        if not self.selection:
            return ''

        def print_pos(pos):
            return " ".join([str(i) for i in pos.round(2)])

        def add_info(indx, atom):
            return "Id: {}; Symbol: {}; Coordinates: ({})<br>".format(indx + 1, atom.symbol, print_pos(atom.position))

        # Find geometric center.
        geom_center = print_pos(np.average(self.structure[self.selection].get_positions(), axis=0))

        # Report coordinates.
        if len(self.selection) == 1:
            return add_info(self.selection[0], self.structure[self.selection[0]])

        # Report coordinates, distance and center.
        if len(self.selection) == 2:
            info = ""
            info += add_info(self.selection[0], self.structure[self.selection[0]])
            info += add_info(self.selection[1], self.structure[self.selection[1]])
            info += "Distance: {}<br>Geometric center: ({})".format(
                self.structure.get_distance(*self.selection).round(2), geom_center)
            return info

        # Report angle geometric center and normal.
        if len(self.selection) == 3:
            angle = self.structure.get_angle(*self.selection).round(2)
            normal = np.cross(*self.structure.get_distances(
                self.selection[1], [self.selection[0], self.selection[2]], mic=False, vector=True))
            normal = normal / np.linalg.norm(normal)
            return "{} atoms selected<br>Geometric center: ({})<br>Angle: {}<br>Normal: ({})".format(
                len(self.selection), geom_center, angle, print_pos(normal))

        # Report  dihedral angle and geometric center.
        if len(self.selection) == 4:
            dihedral = self.structure.get_dihedral(self.selection) * 180 / np.pi
            return "{} atoms selected<br>Geometric center: ({})<br>Dihedral angle: {}".format(
                len(self.selection), geom_center, dihedral.round(2))

        return "{} atoms selected<br>Geometric center: ({})".format(len(self.selection), geom_center)

    @observe('selection')
    def _observe_selection_2(self, _=None):
        self.selection_info.value = self.create_selection_info()


class FolderDataViewer(ipw.VBox):
    """Viewer class for FolderData object.

    :param folder: FolderData object to be viewed
    :type folder: FolderData
    :param downloadable: If True, add link/button to download the content of the selected file in the folder
    :type downloadable: bool"""

    def __init__(self, folder, downloadable=True, **kwargs):
        self._folder = folder
        self.files = ipw.Dropdown(
            options=[obj.name for obj in self._folder.list_objects()],
            description="Select file:",
        )
        self.text = ipw.Textarea(value="",
                                 description='File content:',
                                 layout={
                                     'width': "900px",
                                     'height': '300px'
                                 },
                                 disabled=False)
        self.change_file_view()
        self.files.observe(self.change_file_view, names='value')
        children = [self.files, self.text]
        if downloadable:
            self.download_btn = ipw.Button(description="Download")
            self.download_btn.on_click(self.download)
            children.append(self.download_btn)
        super().__init__(children, **kwargs)

    def change_file_view(self, change=None):  # pylint: disable=unused-argument
        with self._folder.open(self.files.value) as fobj:
            self.text.value = fobj.read()

    def download(self, change=None):  # pylint: disable=unused-argument
        """Prepare for downloading."""
        from IPython.display import Javascript

        payload = base64.b64encode(self._folder.get_object_content(self.files.value).encode()).decode()
        javas = Javascript("""
            var link = document.createElement('a');
            link.href = "data:;base64,{payload}"
            link.download = "{filename}"
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            """.format(payload=payload, filename=self.files.value))
        display(javas)


class BandsDataViewer(ipw.VBox):
    """Viewer class for BandsData object.

    :param bands: BandsData object to be viewed
    :type bands: BandsData"""

    def __init__(self, bands, **kwargs):
        from bokeh.io import show, output_notebook
        from bokeh.models import Span
        from bokeh.plotting import figure
        output_notebook(hide_banner=True)
        out = ipw.Output()
        with out:
            plot_info = bands._get_bandplot_data(cartesian=True, join_symbol="|")  # pylint: disable=protected-access
            # Extract relevant data
            y_data = plot_info['y'].transpose().tolist()
            x_data = [plot_info['x'] for i in range(len(y_data))]
            labels = plot_info['labels']
            # Create the figure
            plot = figure(y_axis_label='Dispersion ({})'.format(bands.units))
            plot.multi_line(x_data, y_data, line_width=2, line_color='red')  # pylint: disable=too-many-function-args
            plot.xaxis.ticker = [l[0] for l in labels]
            # This trick was suggested here: https://github.com/bokeh/bokeh/issues/8166#issuecomment-426124290
            plot.xaxis.major_label_overrides = {int(l[0]) if l[0].is_integer() else l[0]: l[1] for l in labels}
            # Add vertical lines
            plot.renderers.extend(
                [Span(location=l[0], dimension='height', line_color='black', line_width=3) for l in labels])
            show(plot)
        children = [out]
        super().__init__(children, **kwargs)


AIIDA_VIEWER_MAPPING = {
    'data.dict.Dict.': DictViewer,
    'data.structure.StructureData.': StructureDataViewer,
    'data.cif.CifData.': StructureDataViewer,
    'data.folder.FolderData.': FolderDataViewer,
    'data.array.bands.BandsData.': BandsDataViewer,
}
