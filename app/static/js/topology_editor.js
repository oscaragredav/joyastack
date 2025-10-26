// =============================
// Joyastack Topology Editor JS
// =============================

const nodes = new vis.DataSet([]);
const edges = new vis.DataSet([]);
const container = document.getElementById("network");
const network = new vis.Network(container, { nodes, edges }, { interaction: { multiselect: true } });
let counter = 1;
let currentSliceId = null;
let imagesList = [];

// --- Cargar imágenes disponibles desde el backend ---
async function loadImages() {
  try {
    const res = await fetch("/images");
    if (!res.ok) throw new Error("Error HTTP: " + res.status);
    imagesList = await res.json();
    console.log("Imágenes cargadas:", imagesList);
  } catch (err) {
    console.error("Error al cargar imágenes:", err);
    alert("⚠️ No se pudieron cargar las imágenes desde la BD.");
  }
}

// Cargar imágenes al inicio
loadImages();

// --- Agregar VM ---
async function addVM() {
  if (imagesList.length === 0) {
    alert("No hay imágenes disponibles en la base de datos.");
    return;
  }

  const name = prompt("Nombre de la VM:", "VM" + counter);
  if (!name) return;

  const cpu = parseInt(prompt("Número de CPUs:", "1")) || 1;
  const ram = parseInt(prompt("Memoria RAM (MB):", "512")) || 512;
  const disk = parseInt(prompt("Disco (GB):", "3")) || 3;

  // --- Selección de imagen ---
  let imgMenu = "Selecciona la imagen base:\n";
  imagesList.forEach((img, i) => {
    imgMenu += `${i + 1}. ${img.name}\n`;
  });

  const imgIndex = parseInt(prompt(imgMenu, "1")) - 1;
  const selectedImage = imagesList[imgIndex];
  if (!selectedImage) {
    alert("Selección inválida. Cancelando creación de VM.");
    return;
  }

  nodes.add({
    id: name,
    label: `${name}\n${cpu}vCPU / ${ram}MB / ${disk}GB\n${selectedImage.name}`,
    cpu,
    ram,
    disk,
    image_id: selectedImage.id,
  });

  counter++;
  console.log(`VM añadida: ${name} (${selectedImage.name})`);
}

// --- Conectar dos VMs ---
function connectVMs() {
  const selected = network.getSelectedNodes();
  if (selected.length === 2) {
    edges.add({ from: selected[0], to: selected[1] });
  } else {
    alert("Selecciona exactamente dos VMs para conectar.");
  }
}

// --- Eliminar VM seleccionada ---
function deleteVM() {
  const selected = network.getSelectedNodes();
  selected.forEach((id) => nodes.remove(id));
}

// --- Limpiar topología completa ---
function clearAll() {
  if (confirm("¿Borrar toda la topología?")) {
    nodes.clear();
    edges.clear();
    counter = 1;
    currentSliceId = null;
  }
}

// --- Guardar Slice (en BD) ---
async function saveSlice() {
  if (nodes.length === 0) {
    alert("No hay VMs en la topología.");
    return;
  }

  const payload = {
    name: "SliceWeb",
    nodes: nodes.get().map((n) => ({
      label: n.id,
      cpu: parseInt(n.cpu),
      ram: parseInt(n.ram),
      disk: parseInt(n.disk),
      image_id: parseInt(n.image_id),
    })),
    links: edges.get().map((e) => ({ from_vm: e.from, to_vm: e.to })),
  };

  try {
    const res = await fetch("/slices/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) throw new Error("Error HTTP: " + res.status);
    const data = await res.json();
    currentSliceId = data.slice_id;

    alert(`✅ Slice creado exitosamente (ID: ${currentSliceId})`);
    console.log("Slice creado:", data);
  } catch (err) {
    console.error("Error al guardar el slice:", err);
    alert("❌ Error al guardar el slice.");
  }
}

// --- Desplegar Slice ---
async function deploySlice() {
  if (!currentSliceId) {
    alert("Primero debes guardar el slice.");
    return;
  }

  try {
    const res = await fetch(`/slices/deploy/${currentSliceId}`, { method: "POST" });
    if (!res.ok) throw new Error("Error HTTP: " + res.status);
    const data = await res.json();

    alert("🚀 Despliegue iniciado correctamente.");
    console.log("Resultado de despliegue:", data);
  } catch (err) {
    console.error("Error al desplegar slice:", err);
    alert("❌ Error durante el despliegue del slice.");
  }
}
