// upload.js - simple drag-drop and file list preview
document.addEventListener("DOMContentLoaded", function(){
  const dropzone = document.getElementById("dropzone");
  const filesInput = document.getElementById("filesInput");
  const fileList = document.getElementById("fileList");

  function updateList(files){
    fileList.innerHTML = "";
    Array.from(files).forEach((f, idx) => {
      const item = document.createElement("div");
      item.className = "file-item";
      item.innerHTML = `<div>${f.name}</div><div style="font-size:13px;color:#666">${(f.size/1024).toFixed(1)} KB</div>`;
      fileList.appendChild(item);
    });
  }

  // click area triggers file input
  dropzone.addEventListener("click", function(e){
    filesInput.click();
  });

  filesInput.addEventListener("change", function(e){
    updateList(e.target.files);
  });

  // drag & drop handlers
  dropzone.addEventListener("dragover", function(e){
    e.preventDefault();
    dropzone.style.borderColor = "#93c5fd";
    dropzone.style.background = "#f8fbff";
  });
  dropzone.addEventListener("dragleave", function(e){
    dropzone.style.borderColor = "#dfe6f2";
    dropzone.style.background = "";
  });
  dropzone.addEventListener("drop", function(e){
    e.preventDefault();
    dropzone.style.borderColor = "#dfe6f2";
    const dt = e.dataTransfer;
    if (dt && dt.files && dt.files.length){
      filesInput.files = dt.files; // set files into input
      updateList(dt.files);
    }
  });
});
