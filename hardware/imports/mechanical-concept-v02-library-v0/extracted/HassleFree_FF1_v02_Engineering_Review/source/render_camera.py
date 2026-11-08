"""Blender 4.3 render / GLB exporter. Geometry comes from CadQuery STL parts.
Usage: blender -b -t 4 --python source/render_camera.py
"""
import bpy, json, math
from pathlib import Path
from mathutils import Vector

ROOT=Path(__file__).resolve().parents[1]
manifest=json.loads((ROOT/'exports/assembly_manifest.json').read_text())
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
scene=bpy.context.scene
scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=.001
scene.render.engine='CYCLES'
scene.cycles.samples=48
scene.cycles.use_denoising=False
scene.render.resolution_x=1800;scene.render.resolution_y=1300;scene.render.resolution_percentage=100
scene.world.color=(0.7,0.7,0.7)
scene.view_settings.view_transform='AgX'
scene.render.image_settings.file_format='PNG'
scene.render.film_transparent=False
scene.world.use_nodes=True
scene.world.node_tree.nodes['Background'].inputs['Color'].default_value=(0.85,0.87,0.90,1)
scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value=.5

def material(name,color,metal=0,rough=.34):
    # Manifest color values are sRGB. Blender shader colors are scene-linear.
    linear=tuple(c/12.92 if c<=.04045 else ((c+.055)/1.055)**2.4 for c in color[:3])+(color[3],)
    m=bpy.data.materials.new(name);m.diffuse_color=linear;m.use_nodes=True
    bs=m.node_tree.nodes.get('Principled BSDF');bs.inputs['Base Color'].default_value=linear
    bs.inputs['Metallic'].default_value=metal;bs.inputs['Roughness'].default_value=rough
    return m
materials={}
objects={}
for p in manifest:
    bpy.ops.wm.stl_import(filepath=str(ROOT/'exports/mesh'/(p['name']+'.stl')))
    o=bpy.context.object;o.name=p['name'];o.rotation_euler=(math.pi/2,0,0)
    pcolor=p['color']; key=tuple(pcolor)
    if key not in materials:
        metal=.75 if p['group'] in ['chassis','humi','E','EF'] else (.5 if 'carrier' in p['name'] else 0)
        materials[key]=material('Material '+p['name'],pcolor,metal,.28 if metal else .42)
    o.data.materials.append(materials[key])
    # A restrained edge highlight is visualization only, not a CAD fillet claim.
    if p['group'] not in ['electronics','sensor','E','EF','humi']:
        bevel=o.modifiers.new('Visual edge highlight, 0.18 mm','BEVEL');bevel.width=.18;bevel.segments=2
        bevel.affect='EDGES';bevel.limit_method='ANGLE';bevel.angle_limit=.6
        normal=o.modifiers.new('Weighted normals','WEIGHTED_NORMAL');normal.keep_sharp=True
    # Keep CAD planar faces flat. Smoothing every STL triangle creates false
    # dents and radial shading around the annular clearance holes.
    for poly in o.data.polygons:poly.use_smooth=False
    o['design_status']=p['status'];o['cad_units']='mm';o['source']='CadQuery concept, not fit validated'
    objects[p['name']]=o

white=material('Warm white markings',(0.72,.77,.78,1),.05,.55)
accent=material('Muted mint markings',(.15,.67,.59,1),.1,.4)
screenwhite=material('Screen white',(.6,.86,.84,1),0,.3)
bs=screenwhite.node_tree.nodes.get('Principled BSDF');bs.inputs['Emission Color'].default_value=(.4,.75,.68,1);bs.inputs['Emission Strength'].default_value=.4

decals=[]
def front_text(body,x,y,z,size,mat=white):
    c=bpy.data.curves.new(body,'FONT');c.body=body;c.size=size;c.extrude=.012;c.align_x='LEFT'
    o=bpy.data.objects.new(body,c);bpy.context.collection.objects.link(o)
    o.location=(x,-z,y);o.rotation_euler=(math.pi/2,0,0);c.materials.append(mat);decals.append(o);return o
def rear_text(body,x,y,z,size,mat=screenwhite):
    c=bpy.data.curves.new(body,'FONT');c.body=body;c.size=size;c.extrude=.005;c.align_x='LEFT'
    o=bpy.data.objects.new(body,c);bpy.context.collection.objects.link(o)
    o.location=(x,-z,y);o.rotation_euler=(math.pi/2,0,math.pi);c.materials.append(mat);decals.append(o);return o
front_text('HassleFree',24,36,12.14,5.1)
front_text('FF-1',47,-32,12.14,4.4)
front_text('HUMI',-5,-35,12.15,2,accent)
front_text('E',-2,28,18.05,3.2)
# Screen UI is a graphic placeholder, not live firmware.
rear_text('M',40,20,-45.95,5)
rear_text('FF-1    READY',29,21,-45.95,2.1)
rear_text('1/125    F 2.8    ISO 100',40,-16,-45.95,2.35)
rear_text('36 x 24    |    RAW',40,14,-45.95,2)
rear_text('+',12,-1,-45.95,7,accent)
rear_text('MENU',-32,25,-47.03,1.8,white)
rear_text('PLAY',-32,12,-47.03,1.8,white)

# Floor, lights, camera.
floor_mat=material('Warm studio',(0.76,.78,.78,1),0,.8)
bpy.ops.mesh.primitive_plane_add(size=2000,location=(0,0,-48))
floor=bpy.context.object;floor.name='Studio ground';floor.data.materials.append(floor_mat)
def area(name,loc,power,size,color):
    d=bpy.data.lights.new(name,'AREA');d.energy=power;d.shape='DISK';d.size=size;d.color=color
    o=bpy.data.objects.new(name,d);bpy.context.collection.objects.link(o);o.location=loc
    o.rotation_euler=(Vector((0,0,0))-o.location).to_track_quat('-Z','Y').to_euler()
area('Large soft key',(-120,-180,250),1500000,210,(1,.95,.88))
area('Cool fill',(200,-20,120),950000,180,(.83,.91,1))
area('Rim',(-40,150,220),1700000,170,(1,1,1))
d=bpy.data.cameras.new('Camera');camera=bpy.data.objects.new('Camera',d);bpy.context.collection.objects.link(camera);scene.camera=camera
camera.data.type='ORTHO';camera.data.lens=58;camera.data.clip_end=5000

def view(position,target,scale):
    camera.location=position;camera.rotation_euler=(Vector(target)-camera.location).to_track_quat('-Z','Y').to_euler();camera.data.ortho_scale=scale
def assembly(mode='E',explode=False,internal=False):
    for p in manifest:
        o=objects[p['name']];show=p['group'] not in ['E','EF'] or p['group']==mode
        if explode:show=True
        if internal and p['group'] in ['shell','rear','battery','storage','controls']:show=False
        o.hide_render=not show;o.hide_set(not show)
        e=p['explode'] if explode else (0,0,0);o.location=(e[0],-e[2],e[1])
    for o in decals:o.hide_render=explode or internal;o.hide_set(explode or internal)
    floor.hide_render=explode or internal
def render(name):
    scene.render.filepath=str(ROOT/'renders'/name);bpy.ops.render.render(write_still=True)

assembly('E');view((-190,-250,150),(-5,0,1),220)
bpy.ops.wm.save_as_mainfile(filepath=str(ROOT/'exports/HassleFree_FF1_concept.blend'))
render('01_front_E.png')
assembly('E');view((190,240,130),(-5,2,1),220);render('02_rear_controls.png')
assembly('EF');view((-190,-250,150),(-5,-12,1),230);render('03_front_EF.png')
assembly('E',explode=True);view((-330,-420,290),(-10,-8,-15),425);render('04_exploded_assembly.png')
assembly('E',internal=True);view((-180,-245,150),(5,-5,0),190);render('05_optical_chassis.png')
assembly('E',internal=True)
for p in manifest:
    if p['group']=='electronics' and not p['name'].startswith(('28_','29_')):
        objects[p['name']].hide_render=True
view((160,240,150),(5,8,0),190);render('07_rear_service_structure.png')

# Export E assembly only; the STEP files remain the authoritative CAD geometry.
assembly('E');view((-190,-250,150),(-5,0,1),220)
bpy.ops.object.select_all(action='DESELECT')
for p in manifest:
    if p['group']!='EF':objects[p['name']].select_set(True)
for o in decals:o.select_set(True)
selected=list(bpy.context.selected_objects)
for o in selected:o.location*=.001;o.scale*=.001
bpy.ops.export_scene.gltf(filepath=str(ROOT/'exports/HassleFree_FF1_E_preview.glb'),export_format='GLB',use_selection=True,export_apply=True,export_extras=True)
for o in selected:o.location*=1000;o.scale*=1000
bpy.ops.wm.save_as_mainfile(filepath=str(ROOT/'exports/HassleFree_FF1_concept.blend'))
print('FINISHED: six renders, GLB preview and editable Blender scene.')
