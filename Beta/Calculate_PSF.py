# import omero
# Need to pip install cython; pip install findmaxima2d
import omero.scripts as scripts
from omero.gateway import BlitzGateway, FileAnnotationWrapper
from omero.rtypes import rlong, rstring  # , robject
import omero.util.script_utils as script_utils
import numpy as np
from findmaxima2d import find_maxima, find_local_maxima
from scipy import optimize
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from datetime import date
import pandas as pd
import os
'''
To analyse PSFs to quality check microscopes
'''


def log(data):
    """Handle logging or printing in one place."""
    print(data)


def gaussian(x, a, b, c):
    '''
    Function to calculate gaussian
    '''
    return a*np.exp(-np.power(x - b, 2)/(2*np.power(c, 2)))


def twoD_Gaussian(xdata_tuple, amplitude, xo, yo, sigma_x, sigma_y, theta, offset):
    (x, y) = xdata_tuple
    xo = float(xo)
    yo = float(yo)
    a = (np.cos(theta)**2)/(2*sigma_x**2) + (np.sin(theta)**2)/(2*sigma_y**2)
    b = -(np.sin(2*theta))/(4*sigma_x**2) + (np.sin(2*theta))/(4*sigma_y**2)
    c = (np.sin(theta)**2)/(2*sigma_x**2) + (np.cos(theta)**2)/(2*sigma_y**2)
    g = offset + amplitude*np.exp(-(a*((x-xo)**2)
                                    + 2*b*(x-xo)*(y-yo)+c*((y-yo)**2)))
    return g.ravel()


def fitBeads(peaks, image_stack, image_MIP, size, crop):
    '''
    Fit 2D Gaussian to MIP of each bead and fit z along central pixels
    '''
    t = 0
    u = np.linspace(0, crop*2-1, crop*2)
    x, y = np.meshgrid(u, u)
    z_pts = np.linspace(0, size['z']-1, size['z'])
    bxy = [(0, 0, 0, 0, 0, -np.inf, 0),
           (np.inf, np.inf, np.inf, np.inf, np.inf, np.inf, np.inf)]
    bz = [(0, 0, 0, 0), (np.inf, np.inf, np.inf, np.inf)]
    no_beads = 0
    for k in peaks:
        no_beads = no_beads + (len(k))
    # initialise our arrays
    fitxy = np.zeros((no_beads, 7))
    fitz = np.zeros((no_beads, 4))

    for i in range(len(peaks)):
        # crop out bead
        sub = image_MIP[peaks[i, 0] - crop:peaks[i, 0] + crop,
                        peaks[i, 1] - crop:peaks[i, 1] + crop]
        p1 = [np.max(sub), crop, crop, 2, 2, 0, 100]
        p2 = [np.max(sub), size['z']/2, 1, 100]
        # find each axis of the max pixel
        print(peaks)
        z_gauss = image_stack[peaks[0][0], peaks[0][1]]

        try:
            popt, pcov = optimize.curve_fit(
                twoD_Gaussian, (x, y), sub.ravel(), p0=p1, bounds=bxy)
            zpars, zcov = optimize.curve_fit(
                f=gaussian, xdata=z_pts, ydata=z_gauss, p0=p2, bounds=bz)
            # read the fitted parameters to an array
            fitxy[t, :] = popt
            fitz[t, :] = zpars
        except RuntimeError:
            # if the algorithm cannot fit, instead of breaking we set the fitted parameters to NaN
            # fit[:,:,i] = 'NaN'
            fitxy[t, :] = 'NaN'
            fitz[t, :] = 'NaN'
        data_fitted = twoD_Gaussian((x, y), *popt)

    return fitxy, fitz, peaks


def getPeaks(image, script_params, conn):
    '''
    Load the image and process
    '''
    d = script_params["Min_Distance"]
    ntol = script_params["Threshold"]
    c = script_params["Channel"]
    t = script_params["Time_Point"]

    size = {}
    size['x'] = image.getSizeX()
    size['y'] = image.getSizeY()
    size['z'] = image.getSizeZ()

    pixels = image.getPrimaryPixels()
    image_stack = np.zeros((size['x'], size['y'], size['z']))
    for i in range(size['z']):
        image_stack[:, :, i] = pixels.getPlane(i, c, t)

    image_MIP = np.max(image_stack, axis=2)

    fig0 = plt.figure(figsize=(3, 3))
    plt.imshow(image_MIP)

    local_max = find_local_maxima(image_MIP)
    y, x, regs = find_maxima(image_MIP, local_max, ntol)
    peaks = np.stack((y, x), axis=1)

    # If the images show poor peak detection, adjust threshold_abs accordingly.
    # display results
    fig1, axes = plt.subplots(1, 2, sharex=True, sharey=True)
    ax = axes.ravel()
    ax[0].imshow(image_MIP, cmap=plt.cm.gray)
    ax[0].axis('off')
    ax[0].set_title('Beads')

    ax[1].imshow(image_MIP, cmap=plt.cm.gray)
    ax[1].autoscale(False)
    ax[1].plot(peaks[:, 1], peaks[:, 0], 'r.')
    ax[1].axis('off')
    ax[1].set_title('Found peaks')

    Flag = np.zeros(len(peaks))
    for i in range(0, len(peaks)):
        # first check if is an edge one
        if (0 + d < peaks[i, 0] < size['x'] - d) and (0 + d < peaks[i, 1] < size['y'] - d):
            for j in range(0, len(peaks)):
                # ignore if the same coordinate
                if i != j:
                    # discard if the peaks are too close together
                    if (abs(peaks[i, 0] - peaks[j, 0]) < d) and (abs(peaks[i, 1] - peaks[j, 1]) < d):
                        Flag[i] = 1
        else:
            Flag[i] = 1

    # Remove those peaks.
    peaks = peaks[Flag == 0]
    fig2, axes1 = plt.subplots(1, 2, sharex=True, sharey=True)
    ax1 = axes1.ravel()
    ax1[0].imshow(image_MIP, cmap=plt.cm.gray)
    ax1[0].set_title('Beads')

    ax1[1].imshow(image_MIP, cmap=plt.cm.gray)
    ax1[1].autoscale(False)
    ax1[1].plot(peaks[:, 1], peaks[:, 0], 'r.')
    ax1[1].set_title('Found peaks')
    return peaks, image_stack, image_MIP, size, fig0, fig1, fig2


def getImages(conn, script_params):
    """
    Get the images
    """
    message = ""
    objects, log_message = script_utils.get_objects(conn, script_params)
    message += log_message
    if not objects:
        return None, message

    data_type = script_params["Data_Type"]

    if data_type == 'Dataset':
        images = []
        for ds in objects:
            images.extend(list(ds.listChildren()))
        if not images:
            message += "No image found in dataset(s)"
            return None, message
    else:
        images = objects
    return images


def saveResultsToProject(scope, conn, dataset, Rayleigh, Wavelength, NA, acDate):
    project = conn.getObject("Project", dataset.getParent().getId())
    print(project.getId())
    filename = scope + ".csv"
    namespace = "psf.results"
    df = None
    for ann in project.listAnnotations(ns=namespace):
        if isinstance(ann, FileAnnotationWrapper):
            if filename == ann.getFile().getName():
                # Download file
                # make location to download file
                path = os.path.join(os.path.dirname(__file__), "download")
                if not os.path.exists(path):
                    os.makedirs(path)
                file_path = os.path.join(path, ann.getFile().getName())
                # Read file into dataframe
                with open(str(file_path), 'wb') as f:
                    print("\nDownloading file to", file_path, "...")
                    for chunk in ann.getFileInChunks():
                        f.write(chunk)
                df = pd.read_csv(file_path)
                # If acquistion date already has results
                if any(df['Date'] == acDate):
                    # Replace row
                    print(df)
                    df.loc[df['Date'] == str(
                        acDate)] = acDate, Wavelength, NA, Rayleigh['x'], Rayleigh['y'], Rayleigh['z']
                else:
                    # Append file with:
                    new_df = pd.DataFrame({'Date': [acDate],
                                           'Wavelength': [Wavelength],
                                           'Numerical Aperture': [NA],
                                           'Rayleigh x': [Rayleigh['x']],
                                           'Rayleigh y': [Rayleigh['y']],
                                           'Rayleigh z': [Rayleigh['z']]}
                                          )
                    df = df.append(new_df)
    if df is None:
        # Create new file
        df = pd.DataFrame({'Date': [acDate],
                           'Wavelength': [Wavelength],
                           'Numerical Aperture': [NA],
                           'Rayleigh x': [Rayleigh['x']],
                           'Rayleigh y': [Rayleigh['y']],
                           'Rayleigh z': [Rayleigh['z']]}
                          )
    df.to_csv(filename, index=False)
    # create the original file and file annotation (uploads the file)
    file_ann = conn.createFileAnnfromLocalFile(
                filename, mimetype="text/plain", ns=namespace, desc=None)
    project.linkAnnotation(file_ann)

    return df


def getMetadata(channel, image):
    """
    Get the required values from the metadata
    """
    channels = image.getChannels()
    EmWave = channels[channel].getEmissionWave()
    try:
        # SoRa
        md = image.loadOriginalMetadata()
        global_metadata = dict(md[1])
        NA = float(global_metadata['Numerical Aperture'])
    except KeyError:
        # DV2
        NA = image.getInstrument().getObjective()[0].getLensNA().val
    except UnboundLocalError:
        print('No NA found')

    pixelSize = [image.getPixelSizeX(), image.getPixelSizeY(),
                 image.getPixelSizeZ()]

    acDate = image.getAcquisitionDate()

    return EmWave, NA, pixelSize, acDate


def runScript():
    dataTypes = [rstring('Dataset'), rstring('Image')]
    client = scripts.client(
        "PSF_Distiller.py", """Analyse point spread function, return FWHM""",
        scripts.String("Data_Type", optional=False, grouping="01",
                       values=dataTypes, default="Image"),
        scripts.String("Microscope", optional=False, grouping="02",
                       default="DV2"),
        scripts.List("IDs", optional=False, grouping="03",
                     description="""IDs of the images to project"""
                     ).ofType(rlong(0)),
        scripts.Int("Channel", optional=False, grouping="04", default=0,
                    description="Enter one channel"),
        scripts.Int("Time_Point", optional=False, grouping="05", default=0,
                    description="Enter one time point"),
        scripts.Int("Min_Distance", optional=False, grouping="06",
                    description="For peak finding algorithm, aka d"),
        scripts.Int("Crop", optional=False, grouping="07",
                    description="For peak finding algorithm"),
        scripts.Int("Tolerance", optional=False, grouping="08",
                    description="For local maxima function, aka ntol"),
        # scripts.Float("NA", optional=False, grouping="10", description="NA"),
        # scripts.Float("Wavelength", optional=False, grouping="11",
        #              description="Wavelength"),
        version="0.2",
        authors=["Laura Cooper and Claire Mitchell", "CAMDU"],
        institutions=["University of Warwick"],
        contact="camdu@warwick.ac.uk"
        )
    try:
        conn = BlitzGateway(client_obj=client)
        script_params = client.getInputs(unwrap=True)
        images = getImages(conn, script_params)

        for image in images:
            peaks, image_stack, image_MIP, size, MipFig, peak1Fig, peak2Fig = getPeaks(
                image, script_params, conn)
            if not peaks.size:
                print("No peaks found!")
            else:
                fit, peaks = fitBeads(peaks, image_stack, image_MIP,
                                      size, script_params["Crop"])

                Wavelength, NA, pixelSize, acDate = getMetadata(
                                                        script_params["Channel"],
                                                        image)

                # Now we can collect the standard deviations of each bead in all
                # 3 dimensions and convert to Rayleigh range.
                # FWHM = standard deviation * 2 * sqrt(2 * ln(2))
                # Rayleigh range = FWHM * 1.1853
                K = 2*np.sqrt(2*np.log(2))*1.1853
                Rayleigh = {}
                Rayleigh['x'] = np.mean(fit[0, 2, :]*K*pixelSize[0])
                Rayleigh['y'] = np.mean(fit[1, 2, :]*K*pixelSize[1])
                Rayleigh['z'] = np.mean(fit[2, 2, :]*K*pixelSize[2])

                # expected gaussian size
                xres = 0.61 * Wavelength / NA
                # convert to pixels
                zres = 2 * Wavelength / NA

                fig, axes = plt.subplots(3, 3, sharey=True)

                for i in range(0, len(peaks)):
                    # crop out bead
                    sub = image_stack[
                        peaks[i, 0] - script_params["Crop"]:
                            peaks[i, 0] + script_params["Crop"] + 1,
                        peaks[i, 1] - script_params["Crop"]:
                            peaks[i, 1] + script_params["Crop"] + 1, :]
                    # remove background
                    sub = sub - np.mean(sub[0:5, 0:5, 0])
                    # find the maximum pixel
                    maxv = np.amax(sub)
                    maxpx = np.where(sub == maxv)
                    # find each axis of the max pixel
                    x_gauss = sub[:, maxpx[1], maxpx[2]].transpose()[0]
                    y_gauss = sub[maxpx[0], :, maxpx[2]][0]
                    z_gauss = sub[maxpx[0], maxpx[1], :][0]
                    # plot
                    axes[0, 0].plot(x_gauss)
                    axes[0, 1].plot(y_gauss)
                    axes[0, 2].plot(z_gauss)
                    # read the fitted parameters to an array
                    xpars = fit[0, 0:3, i]
                    ypars = fit[1, 0:3, i]
                    zpars = fit[2, 0:3, i]

                    xy_pts = np.linspace(start=0, stop=script_params["Crop"]*2,
                                         num=script_params["Crop"]*2 + 1)
                    z_pts = np.linspace(
                        start=0, stop=size['z']-1, num=size['z'])

                    # Calculate the residuals
                    xres = x_gauss - gaussian(xy_pts, *xpars)
                    yres = y_gauss - gaussian(xy_pts, *ypars)
                    zres = z_gauss - gaussian(z_pts, *zpars)

                    # plot the fit results
                    axes[1, 0].plot(gaussian(xy_pts, *xpars))
                    axes[1, 1].plot(gaussian(xy_pts, *ypars))
                    axes[1, 2].plot(gaussian(z_pts, *zpars))

                    # plot the residuals
                    axes[2, 0].plot(xres)
                    axes[2, 1].plot(yres)
                    axes[2, 2].plot(zres)

                firstPage = plt.figure(figsize=(11.9, 8.27))
                firstPage.clf()
                inputs = "Wavelength: %s,\n Numerical Aperture: %s,\n" % (
                    Wavelength, NA)
                outputs = "Rayleigh x: %s,\n y: %s,\n z:%s" % (
                    Rayleigh['x'], Rayleigh['y'], Rayleigh['z'])
                firstPage.text(0.5, 0.5, inputs+outputs,
                               transform=firstPage.transFigure, size=24,
                               ha="center")

                dataset = conn.getObject("Dataset", image.getParent().getId())
                print(dataset.getId())
                if dataset.getParent() is not None:
                    df = saveResultsToProject(
                        script_params["Microscope"], conn, dataset, Rayleigh,
                        Wavelength, NA, acDate)
                    ax = df.plot(x='Date')
                    lastPage = ax.get_figure()
                else:
                    print('Image not in a project, not saving results')

                # Save figures to file:
                fileName = "DistilledPSF_%s.pdf" % (date.today())
                pdf = PdfPages(fileName)
                pdf.savefig(firstPage)
                pdf.savefig(MipFig)
                pdf.savefig(peak1Fig)
                pdf.savefig(peak2Fig)
                pdf.savefig(fig)
                if dataset.getParent() is not None:
                    pdf.savefig(lastPage)
                pdf.close()
                plt.close('all')
                # create the original file and file annotation (uploads the file)
                namespace = "plots.to.pdf"
                file_ann = conn.createFileAnnfromLocalFile(
                    fileName, mimetype="text/plain", ns=namespace, desc=None)
                image.linkAnnotation(file_ann)

    finally:
        # Cleanup
        client.closeSession()


if __name__ == '__main__':
    runScript()
