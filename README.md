# Journey3Dg
### The g stands for 'Gosh, it has greyscale'

This is a little demo for the Micropython-based Thumby games console.

This is an update of 'Journey3D' which was started in 2021 and had a small release. The previous version was much simpler... this update has various improvements and optimisations, and features 4-level greyscale (with 4x4 ordered dithering to provide 16 shades) and a self-extractor 'installer'.

Since the FR frame-sync signal is unavailable, the PWM to create the greyscale is frame-synced using a controller hack. This is detailed within [the graphics driver](src/ssd1306grey.py), and more details can be found at [the Thumby Grayscale library](https://github.com/Timendus/thumby-grayscale) which uses a modified version of the graphics driver, and is definitely worth checking out!  
Note that the graphics driver requires Micropython v1.19.1 so make sure that your Thumby is updated. This can be done using the online [Code Editor](https://code.thumby.us/).

If you have [ampy](https://github.com/scientifichackers/ampy) installed you can use the makefile to install the application to your Thumby. Run `make ul-src` to do so. If you have the v1.19.1 [mpy-cross](https://pypi.org/project/mpy-cross/) installed then you can build .mpy files to install (`make ul-mpy`), which should take up less space on your Thumby.

You can create self-extracting installers using `make sfx-src` or `make sfx-mpy`, which can then be uploaded using `make ul-sfx-src` or `make ul-sfx-mpy` respectively. If the self-extractor is used, then on the first run the files will be decompressed and saved on to the Thumby. Subsequent runs will use the already installed files.

If you want to provide extra parameters to `ampy`, copy `local.mk.template` to `local.mk` and uncomment and change the `AMPY_ARGS` variable as required. This can be used to provide the device to open for your Thumby if you are not setting an `AMPY_PORT` environment variable.

I'm sure I've left out some important information so get in contact if you have any questions. You can usually find me (Doogle) on the Thumby Discord.
