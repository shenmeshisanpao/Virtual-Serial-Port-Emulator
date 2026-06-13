退出容器后，容器处于“停止”状态。要再次进去打包，请按以下**标准四步走**：

### 1. 唤醒容器
先让后台的容器运行起来：
```bash
sudo docker start sim_packer
```
*(如果你之前没用 `sim_packer` 这个名字，而是用的 `my_packer`，请替换成对应的名字)*

### 2. 进入容器
再次登录进去：
```bash
sudo docker exec -it sim_packer /bin/bash
```

### 3. 【关键】恢复环境变量
**这一步最容易忘！** 每次重新进入容器，系统都会忘记 Miniconda 在哪里，所以必须再次执行：
```bash
export PATH="/opt/miniconda/bin:$PATH"
```
*(如果不执行这句，输入 `pyinstaller` 会提示找不到命令)*

### 4. 执行打包
切换到代码目录并运行打包命令：
```bash
cd /io

pyinstaller --clean --noconfirm --windowed --onefile \
 --name "Instrument_Simulator" \
 main.py
```

打包完成后，你就可以在宿主机的 `dist` 文件夹里看到更新后的程序了。