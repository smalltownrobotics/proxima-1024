/* Voyage viewport: local Hunyuan hulls, weathered materials, fictional worlds,
 * and a coordinate-grounded star chart. Artwork never changes simulation state.
 * Cached assemblies share resources; only route geometry is disposed on exit.
 * Original sources and texture prompts: ASSET_PROVENANCE.md. */
import * as T from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';

const ROOT='/assets/industrial/';
const WORLDS={
  proxima_b:{tint:0xc7b6a0,air:0x7399b6,light:0xffd3a0,rotation:.65},
  ross_128_b:{tint:0xc6c1b8,air:0xb2b7c2,light:0xffe0bd,rotation:2.8},
  trappist_1_e:{tint:0x879ca7,air:0x708eae,light:0xffbc91,rotation:4.5},
};

// === Original meshes have no UVs: triplanar albedo, roughness and detail ===
function hullMaterial(texture,color=0x9ca5ad,scale=.85) {
  const material=new T.MeshStandardMaterial({color,metalness:.52,roughness:.68,envMapIntensity:.2});
  material.onBeforeCompile=shader=>{
    shader.uniforms.hullAtlas={value:texture};shader.uniforms.hullScale={value:scale};
    shader.vertexShader=shader.vertexShader.replace('#include <common>',`#include <common>
      varying vec3 vHullPosition; varying vec3 vHullNormal;`)
      .replace('#include <begin_vertex>',`#include <begin_vertex>
        vHullPosition=position;vHullNormal=normal;`);
    shader.fragmentShader=shader.fragmentShader.replace('#include <common>',`#include <common>
      uniform sampler2D hullAtlas;uniform float hullScale;
      varying vec3 vHullPosition;varying vec3 vHullNormal;
      vec3 hullSample(vec3 p,vec3 n){
        vec3 w=pow(abs(normalize(n)),vec3(6.0));w/=max(dot(w,vec3(1.0)),.0001);
        return texture2D(hullAtlas,p.yz*hullScale).rgb*w.x
          +texture2D(hullAtlas,p.xz*hullScale).rgb*w.y
          +texture2D(hullAtlas,p.xy*hullScale).rgb*w.z;
      }`)
      .replace('#include <color_fragment>',`#include <color_fragment>
        vec3 plate=hullSample(vHullPosition,vHullNormal);
        float wear=dot(plate,vec3(.2126,.7152,.0722));
        diffuseColor.rgb*=mix(vec3(wear),plate,.2)*.95;`)
      .replace('#include <roughnessmap_fragment>',`#include <roughnessmap_fragment>
        roughnessFactor=clamp(.79-wear*.35,.51,.86);`)
      .replace('#include <normal_fragment_maps>',`#include <normal_fragment_maps>
        vec3 sx=normalize(dFdx(-vViewPosition)),sy=normalize(dFdy(-vViewPosition));
        vec3 r1=cross(sy,normal),r2=cross(normal,sx);
        float determinant=dot(sx,r1)*faceDirection;
        normal=normalize(abs(determinant)*normal-sign(determinant)*
          (dFdx(wear)*r1+dFdy(wear)*r2)*.085);`);
  };
  material.customProgramCacheKey=()=>`industrial-hull-v1-${scale}`;return material;
}

export class VoyageScene {
  constructor(canvas) {
    this.canvas=canvas;this.mode='planet';this.options={};this.reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.renderer=new T.WebGLRenderer({canvas,antialias:true,alpha:false});
    this.renderer.setPixelRatio(Math.min(devicePixelRatio,1.75));this.renderer.setClearColor(0x020509);
    this.renderer.outputColorSpace=T.SRGBColorSpace;this.renderer.toneMapping=T.ACESFilmicToneMapping;this.renderer.toneMappingExposure=1.15;
    this.renderer.shadowMap.enabled=true;this.renderer.shadowMap.type=T.PCFShadowMap;
    this.scene=new T.Scene();this.camera=new T.PerspectiveCamera(38,1,.03,2000);
    this.fill=new T.HemisphereLight(0x9caaba,0x10121a,.5);this.scene.add(this.fill);
    this.key=new T.DirectionalLight(0xffe0b9,4.8);this.key.position.set(-4,5,2.5);
    this.key.castShadow=true;this.key.shadow.mapSize.set(1024,1024);this.key.shadow.camera.left=-5;this.key.shadow.camera.right=5;
    this.key.shadow.camera.top=5;this.key.shadow.camera.bottom=-5;this.key.shadow.camera.near=.1;this.key.shadow.camera.far=22;
    this.key.shadow.bias=-.0004;this.key.shadow.normalBias=.018;this.scene.add(this.key);
    this.rim=new T.DirectionalLight(0x9abbdc,2.5);this.rim.position.set(4,1,-3);this.scene.add(this.rim);
    const environment=new RoomEnvironment(),pmrem=new T.PMREMGenerator(this.renderer);
    this.environment=pmrem.fromScene(environment,.06);environment.dispose();pmrem.dispose();this.scene.environment=this.environment.texture;
    this.object=new T.Group();this.route=new T.Group();this.scene.add(this.object,this.route);this.route.visible=false;
    this.labels=[];this.targets={};this.models={};this.architectures={};this.progress=0;this.last=0;this.dragging=false;
    this.observer=new ResizeObserver(()=>this.resize());this.observer.observe(canvas.parentElement);
    this.bindInspection();this.ready=this.load();this.resize();requestAnimationFrame(t=>this.animate(t));
  }

  // === Load once; no CDN, remote model service or decoder at runtime ===
  async load() {
    const textureLoader=new T.TextureLoader(),loader=new GLTFLoader();
    const [stars,targets,hull,planet,sky,...ships]=await Promise.all([
      fetch('/assets/stars.json').then(r=>r.json()),fetch('/assets/sky-targets.json').then(r=>r.json()),
      textureLoader.loadAsync(ROOT+'worn-hull.jpg'),textureLoader.loadAsync(ROOT+'rocky-world.jpg'),textureLoader.loadAsync(ROOT+'deep-space.jpg'),
      ...['cruiser','freighter','spine'].map(name=>loader.loadAsync(ROOT+name+'.glb')),
    ]);
    this.targets=targets.targets;
    for(const texture of [hull,planet,sky]){texture.colorSpace=T.SRGBColorSpace;texture.anisotropy=Math.min(8,this.renderer.capabilities.getMaxAnisotropy());}
    hull.wrapS=hull.wrapT=T.RepeatWrapping;planet.wrapS=T.RepeatWrapping;
    this.hull=hullMaterial(hull);this.darkHull=hullMaterial(hull,0x67737e,.95);this.paleHull=hullMaterial(hull,0xbcc3c7,.85);
    this.trim=new T.MeshStandardMaterial({color:0x48545c,metalness:.8,roughness:.46,envMapIntensity:.4});
    this.lightMaterial=new T.MeshBasicMaterial({color:0xa5c8d3,toneMapped:false});
    for(const [i,name] of ['cruiser','freighter','spine'].entries()){
      const model=ships[i].scene;
      model.traverse(child=>{if(child.isMesh){child.material=this.hull;child.castShadow=true;child.receiveShadow=true;child.geometry.computeVertexNormals();}});
      const center=new T.Box3().setFromObject(model).getCenter(new T.Vector3());model.position.sub(center);this.models[name]=model;
    }
    this.makeStars(stars);this.makePlanet(planet);
    // The panorama is illustrative art, never a layer of the catalog chart.
    this.skyTexture=sky;
    for(const type of ['ship_aurora_ark','ship_stanford_torus','ship_modular_cluster'])this.architectures[type]=this.makeArchitecture(type);
    this.set(this.mode,this.options);this.canvas.dataset.ready='true';
  }
  vector(ra,dec,r) {const a=T.MathUtils.degToRad(ra),d=T.MathUtils.degToRad(dec);return new T.Vector3(r*Math.cos(d)*Math.cos(a),r*Math.sin(d),r*Math.cos(d)*Math.sin(a));}
  makeStars(stars) {
    const positions=[],colors=[],sizes=[];
    for(const star of stars){
      positions.push(...this.vector(star.ra,star.dec,400).toArray());
      const warm=/^[KM]/.test(star.spec||''),bright=Math.max(.1,Math.min(1.5,Math.pow(10,-.18*(star.mag-1))));
      colors.push(bright*(warm?1:.8),bright*(warm?.84:.91),bright*(warm?.66:1));sizes.push(Math.max(1,3.5-star.mag*.35));
    }
    const geometry=new T.BufferGeometry();geometry.setAttribute('position',new T.Float32BufferAttribute(positions,3));
    geometry.setAttribute('color',new T.Float32BufferAttribute(colors,3));geometry.setAttribute('starSize',new T.Float32BufferAttribute(sizes,1));
    this.stars=new T.Points(geometry,new T.ShaderMaterial({transparent:true,depthWrite:false,vertexColors:true,
      uniforms:{pixelRatio:{value:this.renderer.getPixelRatio()}},
      vertexShader:`attribute float starSize;uniform float pixelRatio;varying vec3 starColor;
        void main(){starColor=color;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);gl_PointSize=starSize*pixelRatio;}`,
      fragmentShader:`varying vec3 starColor;void main(){float d=length(gl_PointCoord-.5);float a=1.-smoothstep(.05,.5,d);if(a<.01)discard;gl_FragColor=vec4(starColor,a*.8);}`,
    }));this.scene.add(this.stars);
  }
  makePlanet(texture) {
    this.planet=new T.Group();
    const material=new T.MeshStandardMaterial({map:texture,color:0xc7b6a0,roughness:.96,metalness:0,envMapIntensity:0});
    // Generated atlas edges are blended across longitude. Concept geology is
    // neither calibrated albedo nor a measured elevation map.
    material.onBeforeCompile=shader=>{shader.fragmentShader=shader.fragmentShader.replace('#include <map_fragment>',`
      vec2 uv=vMapUv;vec4 terrain=texture2D(map,uv);
      float seam=.5*(1.-smoothstep(0.,.025,min(uv.x,1.-uv.x)));
      terrain=mix(terrain,texture2D(map,vec2(1.-uv.x,uv.y)),seam);diffuseColor*=terrain;`);};
    this.surface=new T.Mesh(new T.SphereGeometry(1.6,128,80),material);this.planet.add(this.surface);
    this.air=new T.Mesh(new T.SphereGeometry(1.633,96,64),new T.ShaderMaterial({transparent:true,depthWrite:false,blending:T.AdditiveBlending,side:T.FrontSide,
      uniforms:{airColor:{value:new T.Color(0x7399b6)},sun:{value:this.key.position.clone().normalize()}},
      vertexShader:`varying vec3 n;varying vec3 view;varying vec3 worldNormal;
        void main(){vec4 p=modelViewMatrix*vec4(position,1.);n=normalize(normalMatrix*normal);view=normalize(-p.xyz);worldNormal=normalize(mat3(modelMatrix)*normal);gl_Position=projectionMatrix*p;}`,
      fragmentShader:`uniform vec3 airColor;uniform vec3 sun;varying vec3 n;varying vec3 view;varying vec3 worldNormal;
        void main(){float edge=pow(1.-max(0.,dot(normalize(n),normalize(view))),4.5);
        float day=smoothstep(-.3,.6,dot(normalize(worldNormal),sun));gl_FragColor=vec4(airColor,edge*day*.33);}`,
    }));this.planet.add(this.air);
  }

  // === Concept assemblies: locally generated solid hulls plus explicit habitats ===
  mesh(geometry,material=this.hull) {const m=new T.Mesh(geometry,material);m.castShadow=true;m.receiveShadow=true;return m;}
  beam(a,b,r=.035,material=this.trim) {const d=b.clone().sub(a),m=this.mesh(new T.CylinderGeometry(r,r,d.length(),8),material);m.position.copy(a).add(b).multiplyScalar(.5);m.quaternion.setFromUnitVectors(new T.Vector3(0,1,0),d.normalize());return m;}
  hullCopy(name,scale=1) {const group=new T.Group();group.add(this.models[name].clone(true));group.scale.setScalar(scale);return group;}
  habitat(radius,z) {
    const group=new T.Group();group.position.z=z;group.add(this.mesh(new T.TorusGeometry(radius,.16,12,96),this.paleHull));
    for(let i=0;i<16;i++){
      const a=i*Math.PI/8,x=Math.cos(a)*radius,y=Math.sin(a)*radius;
      const module=this.mesh(new T.BoxGeometry(.24,.31,.44),i%4===0?this.darkHull:this.hull);module.position.set(x,y,0);module.rotation.z=a-Math.PI/2;group.add(module);
      const window=this.mesh(new T.BoxGeometry(.085,.018,.015),this.lightMaterial);window.position.set(x*1.006,y*1.006,.232);window.rotation.z=a-Math.PI/2;group.add(window);
      if(i%4===0)group.add(this.beam(new T.Vector3(),new T.Vector3(x*.94,y*.94,0),.037));
    }
    const rail=this.mesh(new T.TorusGeometry(radius+.025,.027,6,96),this.trim);rail.position.z=.19;group.add(rail);
    const rail2=rail.clone();rail2.position.z=-.19;group.add(rail2);group.userData.rotor=true;return group;
  }
  makeArchitecture(type) {
    const group=new T.Group();
    if(type==='ship_modular_cluster'){
      group.add(this.hullCopy('freighter',1.75));
      for(let i=0;i<12;i++){
        const side=i<6?-1:1,row=i%6,z=(row-2.5)*.48;
        const pod=this.mesh(new T.CapsuleGeometry(.17,.1,5,16),row%3===0?this.paleHull:this.hull);pod.rotation.x=Math.PI/2;pod.position.set(side*1.21,-.08,z);group.add(pod);
        group.add(this.beam(new T.Vector3(side*.65,-.08,z),pod.position,.06));
      }
    }else{
      const torus=type==='ship_stanford_torus';group.add(this.hullCopy(torus?'spine':'cruiser',torus?2.05:1.65));
      group.add(this.habitat(torus?1.54:1.62,torus?0:-.78));if(!torus)group.add(this.habitat(1.62,.86));
      for(const side of [-1,1])for(let i=0;i<5;i++){
        const radiator=this.mesh(new T.BoxGeometry(.38,.025,.82),this.darkHull);radiator.position.set(side*(.35+i*.15),-.26,-1.55);radiator.rotation.z=side*.2;group.add(radiator);
      }
    }
    // Practical light cores, not fabricated telemetry or a claim of thrust.
    for(const x of [-.36,0,.36]){
      const nozzle=this.mesh(new T.CylinderGeometry(.14,.21,.36,16,1,true),this.trim);nozzle.rotation.x=Math.PI/2;nozzle.position.set(x,-.14,-1.87);group.add(nozzle);
      const core=this.mesh(new T.CircleGeometry(.105,16),new T.MeshBasicMaterial({color:0x8db6cc,toneMapped:false}));core.rotation.y=Math.PI;core.position.set(x,-.14,-2.055);group.add(core);
    }
    group.rotation.set(.26,-.62,-.12);return group;
  }

  // === Mode transitions / pointer inspection / responsive framing ===
  bindInspection() {
    let previous;
    this.canvas.addEventListener('pointerdown',event=>{if(this.mode==='route')return;this.dragging=true;previous={x:event.clientX,y:event.clientY};this.canvas.setPointerCapture(event.pointerId);});
    this.canvas.addEventListener('pointermove',event=>{if(!this.dragging)return;this.dirty=true;this.object.rotation.y+=(event.clientX-previous.x)*.008;this.object.rotation.x=T.MathUtils.clamp(this.object.rotation.x+(event.clientY-previous.y)*.005,-.6,.6);previous={x:event.clientX,y:event.clientY};});
    const release=()=>{this.dragging=false;};this.canvas.addEventListener('pointerup',release);this.canvas.addEventListener('pointercancel',release);
    matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change',event=>{this.reduced=event.matches;this.dirty=true;});
  }
  clear() {
    this.object.clear();
    for(const child of [...this.route.children]){this.route.remove(child);if(child.userData.shared)continue;child.geometry?.dispose();child.material?.dispose();}
    this.labels=[];this.marker=null;this.routeEnd=null;this.completed=null;
  }
  set(mode,options={}) {
    this.mode=mode;this.options=options;this.clear();this.object.visible=mode!=='route';this.route.visible=mode==='route';this.canvas.dataset.mode=mode;this.dirty=true;
    this.canvas.setAttribute('aria-label',mode==='route'?'Catalog star route with enlarged ship marker':'Rotating concept preview. Drag to inspect.');
    this.scene.background=mode==='route'?null:this.skyTexture;this.scene.backgroundIntensity=.55;
    if(this.stars)this.stars.visible=mode==='route';
    this.fill.intensity=mode==='planet'?.06:.28;this.rim.intensity=mode==='planet'?0:3.5;this.key.color.set(0xffefdb);this.key.intensity=mode==='planet'?3.3:4.2;
    this.scene.environment=mode==='planet'?null:this.environment.texture;this.key.castShadow=mode==='architecture';
    this.camera.up.set(0,1,0);this.object.rotation.set(0,0,0);this.object.position.set(0,.4,0);
    if(mode==='route'){if(this.targets[options.destination])this.buildRoute(options);return;}
    if(mode==='planet'&&this.planet){
      const world=WORLDS[options.destination]||WORLDS.proxima_b;this.surface.material.color.set(world.tint);this.planet.rotation.set(.08,world.rotation,-.18);
      this.air.material.uniforms.airColor.value.set(world.air);this.key.color.set(world.light);this.object.add(this.planet);
    }else if(this.architectures[options.ship||'ship_aurora_ark'])this.object.add(this.architectures[options.ship||'ship_aurora_ark']);
    this.resize();
  }
  resize() {
    this.dirty=true;
    const {width,height}=this.canvas.parentElement.getBoundingClientRect();if(!width||!height)return;
    this.renderer.setSize(width,height,false);this.camera.aspect=width/height;this.camera.updateProjectionMatrix();
    if(this.mode==='route'&&this.routeEnd){
      const end=this.routeEnd,normal=end.clone().cross(new T.Vector3(0,1,0)).normalize();this.camera.up.copy(normal.clone().cross(end).normalize());
      this.camera.position.copy(end).multiplyScalar(.5).addScaledVector(normal,end.length()*Math.max(1.55,1.5/this.camera.aspect));this.camera.lookAt(end.clone().multiplyScalar(.5));
      if(this.marker){this.marker.quaternion.copy(this.camera.quaternion);this.marker.rotateY(-.6);this.marker.rotateX(.4);}
    }else{
      const radius=this.mode==='planet'?1.6:2.15;
      const distance=radius/(Math.sin(T.MathUtils.degToRad(19))*Math.min(1,this.camera.aspect))*(this.mode==='planet'?1.26:1.36);
      this.camera.position.set(0,this.mode==='planet'?.15:1.8,distance);this.camera.lookAt(0,0,0);this.object.position.y=height<270?0:.48;
      this.object.position.x=height<270&&this.camera.aspect>1.4?(this.mode==='planet'?.75:1.15):0;
    }
    // BSC supplies angular directions only. Recenter the sky on the observer:
    // moving a chart camera must not invent parallax from a 400-unit shell.
    this.stars?.position.copy(this.camera.position);
  }

  // === Chart symbols are enlarged; positions/progress retain their data ===
  buildRoute(options) {
    const target=this.targets[options.destination],end=this.vector(target.ra,target.dec,target.distance),origin=new T.Vector3(),radius=Math.max(.035,target.distance*.009);
    for(const [point,color,label] of [[origin,0xeab44c,'SOL'],[end,0xa3cdd4,target.name.toUpperCase()]]){
      const star=new T.Mesh(new T.SphereGeometry(radius,18,12),new T.MeshBasicMaterial({color}));star.position.copy(point);this.route.add(star);this.labels.push({point,label});
    }
    const line=new T.Line(new T.BufferGeometry().setFromPoints([origin,end]),new T.LineDashedMaterial({color:0x547a82,dashSize:target.distance*.025,gapSize:target.distance*.015}));line.computeLineDistances();this.route.add(line);
    this.completed=new T.Line(new T.BufferGeometry().setFromPoints([origin,origin]),new T.LineBasicMaterial({color:0x39c6cb}));this.route.add(this.completed);
    this.marker=new T.Group();this.marker.userData.shared=true;const architecture=this.architectures[options.ship||'ship_aurora_ark'];
    if(architecture){const copy=architecture.clone(true);copy.scale.setScalar(target.distance*.032);this.marker.add(copy);}
    this.route.add(this.marker);this.routeEnd=end;
    for(const [id,star] of Object.entries(this.targets)){
      if(id===options.destination)continue;const dot=new T.Mesh(new T.SphereGeometry(radius*.55,8,8),new T.MeshBasicMaterial({color:0x617f89}));dot.position.copy(this.vector(star.ra,star.dec,star.distance));this.route.add(dot);
    }
    this.setProgress(options.progress||0);this.resize();
  }
  setProgress(value) {
    this.dirty=true;
    this.progress=T.MathUtils.clamp(value,0,1);this.options.progress=this.progress;
    if(this.routeEnd&&this.marker){this.marker.position.copy(this.routeEnd).multiplyScalar(this.progress);this.completed.geometry.setFromPoints([new T.Vector3(),this.marker.position.clone()]);}
  }
  animate(t) {
    requestAnimationFrame(t=>this.animate(t));if(!this.canvas.offsetWidth||document.hidden)return;
    // A still chart/reduced-motion preview need not saturate the GPU. Cap the
    // cinematic turntable at 30 fps; UI/data rendering is independent of this.
    if((this.reduced||this.mode==='route')&&!this.dirty)return;
    if(t-this.last<32)return;const dt=Math.min(.05,(t-this.last)/1000);this.last=t;this.dirty=false;
    if(!this.reduced&&!this.dragging&&this.mode!=='route'){
      this.object.rotation.y+=dt*(this.mode==='planet'?.038:.06);
      if(this.mode==='architecture')this.object.traverse(child=>{if(child.userData.rotor)child.rotation.z+=dt*.04;});
    }
    this.renderer.render(this.scene,this.camera);
    if(this.mode==='route'&&this.onLabels)this.onLabels(this.labels.map(x=>{const p=x.point.clone().project(this.camera);return{label:x.label,x:(p.x+1)*50,y:(1-p.y)*50};}));
  }
}
