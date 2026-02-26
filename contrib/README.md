# Hootcam Server – service and extras

## systemd service (run on boot)

Complete the main [Setup](../README.md#setup) first (venv with `--system-site-packages` and picamera2/ffmpeg). Then see the main [README](../README.md#running-as-a-service) for full instructions. Summary:

1. Copy the unit file:  
   `sudo cp contrib/hootcam-server.service /etc/systemd/system/`
2. If your install path is not `/home/pi/hootcam-server`, edit the unit:  
   `sudo nano /etc/systemd/system/hootcam-server.service`  
   Update `WorkingDirectory` and the path in `ExecStart`.
3. Optional: create `/etc/hootcam-server.env` with `HOOTCAM_TARGET_DIR=/mnt/ssd/hootcam-server`, then in the unit file uncomment the `EnvironmentFile=` line.
4. Enable and start:  
   `sudo systemctl daemon-reload`  
   `sudo systemctl enable hootcam-server`  
   `sudo systemctl start hootcam-server`  
   `sudo systemctl status hootcam-server`
