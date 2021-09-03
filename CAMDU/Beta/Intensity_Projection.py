# import the omero package and the omero.scripts package.
import omero
import omero.scripts as scripts
from omero.gateway import BlitzGateway, DatasetWrapper
from omero.rtypes import rlong, rstring, robject
import omero.util.script_utils as script_utils
import numpy as np
'''
Slow, but low memory usage
'''


def log(data):
    """Handle logging or printing in one place."""
    print(data)


def copyMetadata(conn, newImage, image):
    """
    Copy important metadata
    Reload to prevent update conflicts
    """
    newImage = conn.getObject("Image", newImage.getId())
    new_pixs = newImage.getPrimaryPixels()._obj
    old_pixs = image.getPrimaryPixels()._obj
    new_pixs.setPhysicalSizeX(old_pixs.getPhysicalSizeX())
    new_pixs.setPhysicalSizeY(old_pixs.getPhysicalSizeY())
    new_pixs.setPhysicalSizeZ(old_pixs.getPhysicalSizeZ())
    conn.getUpdateService().saveObject(new_pixs)
    for old_channels, new_channels in zip(image.getChannels(),
                                          newImage.getChannels()):
        new_LogicChan = new_channels._obj.getLogicalChannel()
        new_LogicChan.setName(rstring(old_channels.getLabel()))
        new_LogicChan.setEmissionWave(old_channels.getEmissionWave(units=True))
        new_LogicChan.setExcitationWave(
            old_channels.getExcitationWave(units=True))
        conn.getUpdateService().saveObject(new_LogicChan)

    if newImage._prepareRenderingEngine():
        newImage._re.resetDefaultSettings(True)


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


def tileGenerator(new_Z, C, T, Z, pixels, roi):
    """
    Set up generator of 2D numpy arrays, each of which is a MIP
    To be passed to createImage method so must be order z, c, t
    """
    for s in roi.copyShapes():
        if type(s) == omero.model.RectangleI:
            x, y = s.getX().getValue(), s.getY().getValue()
            w, h = s.getWidth().getValue(), s.getHeight().getValue()
            for z in range(new_Z):
                for c in range(C):
                    for t in range(T):
                        for i in range(Z[0], Z[1]):
                            plane = pixels.getTile(i, c, t, (x, y, w, h))
                            if 'new_plane' not in locals():
                                new_plane = plane
                            else:
                                # Replace pixel values if larger
                                new_plane = np.where(
                                    np.greater(plane, new_plane), plane,
                                    new_plane)
                        yield new_plane


def planeGenerator(new_Z, C, T, Z, pixels):
    """
    Set up generator of 2D numpy arrays, each of which is a MIP
    To be passed to createImage method so must be order z, c, t
    """
    for z in range(new_Z):
        for c in range(C):
            for t in range(T):
                for i in range(Z[0], Z[1]):
                    plane = pixels.getPlane(i, c, t)
                    if 'new_plane' not in locals():
                        new_plane = plane
                    else:
                        # Replace pixel values if larger
                        new_plane = np.where(np.greater(plane, new_plane),
                                             plane, new_plane)
                yield new_plane


def runScript():
    dataTypes = [rstring('Dataset'), rstring('Image')]
    client = scripts.client(
        "Max_Projection.py", """Creates a new image of the maximum intensity \
        projection in Z from an existing image""",
        scripts.String(
            "Data_Type", optional=False, grouping="01", values=dataTypes,
            default="Image"),
        scripts.List(
            "IDs", optional=False, grouping="02",
            description="""IDs of the images to project""").ofType(rlong(0)),
        scripts.Int(
            "First_Z", grouping="03", min=1,
            description="First Z plane to project, default is first plane"),
        scripts.Int(
            "Last_Z", grouping="03", min=1,
            description="Last Z plane to project, default is last plane"),
        scripts.Bool(
            "Apply_to_ROIs_only", grouping="04", default=False,
            description="Apply maximum projection only to rectangular ROIs, \
            if not rectangular ROIs found, image will be skipped"),
        scripts.String(
            "Dataset_Name", grouping="05",
            description="To save projections to new dataset, enter it's name. \
            To save projections to existing dataset, leave blank"),

        version="0.1",
        authors=["Laura Cooper", "CAMDU"],
        institutions=["University of Warwick"],
        contact="camdu@warwick.ac.uk"
        )
    try:
        conn = BlitzGateway(client_obj=client)
        script_params = client.getInputs(unwrap=True)
        images = getImages(conn, script_params)

        # Create new dataset if Dataset_Name is defined
        if "Dataset_Name" in script_params:
            new_dataset = DatasetWrapper(conn, omero.model.DatasetI())
            new_dataset.setName(script_params["Dataset_Name"])
            new_dataset.save()

        for image in images:
            # If Dataset_Name empty user existing, use new one if not.
            if "Dataset_Name" in script_params:
                dataset = new_dataset
            else:
                dataset = image.getParent()

            Z, C, T = image.getSizeZ(), image.getSizeC(), image.getSizeT()
            if "First_Z" in script_params:
                Z1 = [script_params["First_Z"], Z]
            else:
                Z1 = [1, Z]
            if "Last_Z" in script_params:
                Z1[1] = script_params["Last_Z"]
            # Skip image if Z dimension is 1 or if given Z range is less than 1
            if (Z != 1) or ((Z1[1]-Z1[0]) >= 1):
                # Get plane as numpy array
                pixels = image.getPrimaryPixels()
                if script_params["Apply_to_ROIs_only"]:
                    roi_service = conn.getROIService()
                    result = roi_service.findByImage(image.getId(), None)
                    if result is not None:
                        for roi in result:
                            name = "%s_%s_MAX" % (image.getName(), roi.getId())
                            desc = ("Maximum intensity projection in Z of \
                                    Image ID: %s, ROI ID: %s"
                                    % (image.getId(), roi.getId()))
                            newImage = conn.createImageFromNumpySeq(
                                tileGenerator(1, C, T, Z1, pixels, roi), name,
                                1, C, T, description=desc, dataset=dataset)
                else:
                    name = "%s_MAX" % image.getName()
                    desc = ("Maximum intensity projection in Z of Image ID: %s"
                            % image.getId())
                    newImage = conn.createImageFromNumpySeq(
                        planeGenerator(1, C, T, Z1, pixels), name, 1, C, T,
                        description=desc, dataset=dataset)

                copyMetadata(conn, newImage, image)

                client.setOutput("New Image", robject(newImage._obj))
    finally:
        # Cleanup
        client.closeSession()


if __name__ == '__main__':
    runScript()